# Per-Order Decision Attribution and Rationale — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist, for every intent that reaches `guards.check`, which strategy produced it and the indicator context that justified it — durably in the DB for audit and evaluation, compactly in the logs for operations.

**Architecture:** `execute()` is renamed to a private inner function returning `(result, intent)`, and a thin public `execute()` wrapper records exactly one `order_decisions` row per call. That single choke point makes it structurally impossible to miss one of the four return paths. The human-readable sentence is derived from stored fields at read time, never persisted.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (no ORM), `pytest`, `ruff`, `mypy`, `uv`.

**Spec:** `docs/superpowers/specs/2026-07-19-keel-order-decision-audit-design.md`

> ⚠️ **HARD DEPENDENCY — do not start this plan first.** It takes `SCHEMA_VERSION` **3 → 4** and assumes `docs/superpowers/plans/2026-07-19-keel-trade-outcomes-and-streak-breaker.md` has already landed (that plan takes 2 → 3). Starting here would claim version 3 and collide. Verify with `grep '^SCHEMA_VERSION' keel/data/db.py` — it must read `3` before Task 1.

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, `select = ["E", "F", "I", "UP"]`, `ignore = ["UP042"]`.
- Money and prices are `Decimal` in the Python API and exact `TEXT` in SQLite. **Never `float`.**
- `keel_core` is stdlib-only and must stay dependency-free.
- `mypy` strict applies to `keel_broker_*` only; `keel.*`, `tests.*`, `keel_core.*` stay `ignore_errors = true`.
- `uv run pytest tests/baseline/ -v` must pass with `tests/fixtures/baseline_backtest.json` **byte-unchanged** at the end of every task.
- Run everything through `uv run`. Never commit `keel.db`, `*.log`, or `transactions/`.
- After every task: `uv run pytest -q && uv run ruff check . && uv run mypy` must pass.

### Test-authoring rule (binds every task)

**Every positive assertion is paired with its negative.** A test that only asserts a row appears would pass against a writer that records everything indiscriminately; a test that only asserts absence would pass against a writer that records nothing. This repo has shipped two rails that could not fire — treat "the suite is green" as insufficient evidence, and where a step says to verify by mutation, do it.

---

### Task 1: The `order_decisions` table and repository access

**Files:**
- Modify: `keel/data/db.py` (`SCHEMA_VERSION`, `_SCHEMA_STATEMENTS`, `_MIGRATIONS`)
- Modify: `keel/data/repository.py`
- Test: `tests/data/test_order_decisions.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Repository.insert_order_decision(decision: dict) -> int` and `Repository.get_order_decisions(since_ts: int | None = None, rule_name: str | None = None) -> list[dict]`. Task 2 calls these exact names.

- [ ] **Step 1: Confirm the dependency has landed**

Run: `uv run grep '^SCHEMA_VERSION' keel/data/db.py`
Expected: `SCHEMA_VERSION = 3`. If it reads `2`, **stop** — the trade-outcomes plan has not been executed and this plan will collide on the version number.

- [ ] **Step 2: Write the failing tests**

Create `tests/data/test_order_decisions.py`:

```python
"""Round-trip tests for order_decisions -- the per-decision audit record.

`order_id` is nullable and that is load-bearing: a vetoed intent returns from `execute()` before
any `orders` row exists, so a NOT NULL column here would silently force the design back to
placed-orders-only.
"""

from __future__ import annotations

import json
from decimal import Decimal

from keel.data import db
from keel.data.repository import Repository


def _repo() -> Repository:
    conn = db.connect(":memory:")
    db.migrate(conn)
    return Repository(conn)


def _decision(**overrides: object) -> dict:
    base: dict = {
        "ts": 1_800_000_000,
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_id": None,
        "rule_name": "turtle_breakout",
        "cts_score": 8,
        "entry_technique": "limit",
        "entry": Decimal("50000"),
        "stop": Decimal("49000"),
        "target": Decimal("53000"),
        "notional": Decimal("100"),
        "is_dca": False,
        "disposition": "placed",
        "reason": "placed",
        "vetoed_by": None,
        "context": {"adx": "31.2", "donchian_high": "49800"},
    }
    base.update(overrides)
    return base


def test_schema_is_at_version_4() -> None:
    conn = db.connect(":memory:")
    db.migrate(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 4
    assert db.SCHEMA_VERSION == 4


def test_fresh_database_has_no_decisions() -> None:
    """No backfill: the rationale was never computed for historical orders, and inventing it
    would fabricate an audit trail."""
    assert _repo().get_order_decisions() == []


def test_insert_then_get_round_trips() -> None:
    repo = _repo()
    repo.insert_order_decision(_decision())
    rows = repo.get_order_decisions()
    assert len(rows) == 1
    assert rows[0]["rule_name"] == "turtle_breakout"
    assert rows[0]["cts_score"] == 8
    assert rows[0]["entry"] == Decimal("50000")
    assert isinstance(rows[0]["entry"], Decimal)


def test_a_vetoed_decision_stores_a_null_order_id_and_the_rail_names() -> None:
    repo = _repo()
    repo.insert_order_decision(
        _decision(
            order_id=None,
            disposition="vetoed",
            reason="vetoed by guards: per_order_cap: ...",
            vetoed_by=["per_order_cap", "stale_data"],
        )
    )
    row = repo.get_order_decisions()[0]
    assert row["order_id"] is None
    assert row["disposition"] == "vetoed"
    assert row["vetoed_by"] == ["per_order_cap", "stale_data"]


def test_a_placed_decision_stores_its_order_id_and_no_veto(placed_order_id: int = 1) -> None:
    """The negative control for the test above -- same table, opposite shape."""
    repo = _repo()
    repo.insert_order_decision(_decision(order_id=None, disposition="placed", vetoed_by=None))
    row = repo.get_order_decisions()[0]
    assert row["disposition"] == "placed"
    assert row["vetoed_by"] is None


def test_the_context_snapshot_round_trips_exactly() -> None:
    repo = _repo()
    repo.insert_order_decision(_decision(context={"ema_fan": [8, 20, 50], "rsi": "81.4"}))
    assert repo.get_order_decisions()[0]["context"] == {"ema_fan": [8, 20, 50], "rsi": "81.4"}


def test_context_is_stored_as_json_text_not_a_repr() -> None:
    """A Python repr would not survive a query from any other tool."""
    repo = _repo()
    repo.insert_order_decision(_decision(context={"adx": "31.2"}))
    raw = repo._conn.execute("SELECT context FROM order_decisions").fetchone()["context"]
    assert json.loads(raw) == {"adx": "31.2"}


def test_optional_price_fields_may_be_none() -> None:
    """An EXIT signal carries no setup, so entry/stop/target are absent."""
    repo = _repo()
    repo.insert_order_decision(
        _decision(entry=None, stop=None, target=None, cts_score=None, entry_technique=None)
    )
    row = repo.get_order_decisions()[0]
    assert row["entry"] is None
    assert row["cts_score"] is None


def test_filters_by_rule_name_and_since_ts() -> None:
    repo = _repo()
    repo.insert_order_decision(_decision(ts=1_000, rule_name="turtle_breakout"))
    repo.insert_order_decision(_decision(ts=2_000, rule_name="dca"))
    assert len(repo.get_order_decisions(rule_name="dca")) == 1
    assert len(repo.get_order_decisions(since_ts=1_500)) == 1
    assert len(repo.get_order_decisions()) == 2


def test_decisions_come_back_oldest_first() -> None:
    repo = _repo()
    repo.insert_order_decision(_decision(ts=2_000))
    repo.insert_order_decision(_decision(ts=1_000))
    assert [r["ts"] for r in repo.get_order_decisions()] == [1_000, 2_000]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/data/test_order_decisions.py -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'insert_order_decision'`

- [ ] **Step 4: Add the table and bump the schema version**

In `keel/data/db.py`, change `SCHEMA_VERSION = 4`, then append to `_SCHEMA_STATEMENTS`:

```python
    """
    CREATE TABLE IF NOT EXISTS order_decisions (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts               INTEGER NOT NULL,
        product_id       TEXT NOT NULL,
        side             TEXT NOT NULL,
        order_id         INTEGER,
        rule_name        TEXT NOT NULL,
        cts_score        INTEGER,
        entry_technique  TEXT,
        entry            TEXT,
        stop             TEXT,
        target           TEXT,
        notional         TEXT,
        is_dca           INTEGER NOT NULL,
        disposition      TEXT NOT NULL,
        reason           TEXT NOT NULL,
        vetoed_by        TEXT,
        context          TEXT NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_order_decisions_ts ON order_decisions(ts)",
    "CREATE INDEX IF NOT EXISTS idx_order_decisions_rule ON order_decisions(rule_name)",
```

Add the migration step and register it:

```python
def _migrate_v4_order_decisions(conn: sqlite3.Connection) -> None:
    """v4 adds `order_decisions`. Table creation is handled by `_SCHEMA_STATEMENTS`; there is
    deliberately NO backfill.

    Historical orders have no recoverable rationale -- it was never computed for them. Inventing
    one would fabricate an audit trail, which is worse than an honest gap.
    """


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    2: _migrate_v2_broker_subscriptions,
    3: _migrate_v3_trade_outcomes,
    4: _migrate_v4_order_decisions,
}
```

- [ ] **Step 5: Add the repository methods**

In `keel/data/repository.py`, add after the trade-outcomes section:

```python
    # -- order decisions (audit + continuous evaluation) --------------------

    def insert_order_decision(self, decision: dict[str, Any]) -> int:
        """Append one decision. `order_id` is NULL for a vetoed intent -- no order row exists."""
        cursor = self._conn.execute(
            """
            INSERT INTO order_decisions (
                ts, product_id, side, order_id, rule_name, cts_score, entry_technique,
                entry, stop, target, notional, is_dca, disposition, reason, vetoed_by, context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(decision["ts"]),
                decision["product_id"],
                decision["side"],
                decision["order_id"],
                decision["rule_name"],
                decision["cts_score"],
                decision["entry_technique"],
                _dec_to_text(decision["entry"]),
                _dec_to_text(decision["stop"]),
                _dec_to_text(decision["target"]),
                _dec_to_text(decision["notional"]),
                1 if decision["is_dca"] else 0,
                decision["disposition"],
                decision["reason"],
                None if decision["vetoed_by"] is None else json.dumps(decision["vetoed_by"]),
                json.dumps(decision["context"], default=_json_default),
            ),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_order_decisions(
        self, since_ts: int | None = None, rule_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Decisions, OLDEST FIRST. Both filters are optional and combine with AND."""
        clauses: list[str] = []
        params: list[Any] = []
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        if rule_name is not None:
            clauses.append("rule_name = ?")
            params.append(rule_name)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM order_decisions{where} ORDER BY ts, id", params
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ts": int(row["ts"]),
                "product_id": row["product_id"],
                "side": row["side"],
                "order_id": None if row["order_id"] is None else int(row["order_id"]),
                "rule_name": row["rule_name"],
                "cts_score": None if row["cts_score"] is None else int(row["cts_score"]),
                "entry_technique": row["entry_technique"],
                "entry": _text_to_dec(row["entry"]),
                "stop": _text_to_dec(row["stop"]),
                "target": _text_to_dec(row["target"]),
                "notional": _text_to_dec(row["notional"]),
                "is_dca": bool(row["is_dca"]),
                "disposition": row["disposition"],
                "reason": row["reason"],
                "vetoed_by": None if row["vetoed_by"] is None else json.loads(row["vetoed_by"]),
                "context": json.loads(row["context"]),
            }
            for row in rows
        ]
```

`json` and `_json_default` are already imported in this module (used by `set_state`).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/data/test_order_decisions.py -v`
Expected: 10 passed.

- [ ] **Step 7: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: order_decisions table and typed repository access"
```

---

### Task 2: Record one decision per `execute()` call

`execute()` has **four** return points. Rather than writing at each (where a future fifth would be silently missed), the current body becomes a private inner function and a thin public wrapper records exactly once.

**Files:**
- Modify: `keel/execution/executor.py`
- Test: `tests/execution/test_order_decisions.py`

**Interfaces:**
- Consumes: `Repository.insert_order_decision` (Task 1).
- Produces: `keel.execution.executor.execute` keeps its existing signature and return type. Task 3 depends on `_order_row` gaining a `rule_id`.

- [ ] **Step 1: Write the failing tests**

Create `tests/execution/test_order_decisions.py`:

```python
"""Every intent reaching guards.check must leave exactly one decision row.

`execute()` has four return paths (vetoed / confirm-refused / broker-rejected / placed) plus one
early return BEFORE guards for a no-op SELL. The early return must NOT record -- it never reached
guards, which is the spec's scope boundary.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from keel.data.db import connect, migrate
from keel.data.repository import Repository
from keel.execution import executor

NOW_TS = 1_700_000_000


def _repo() -> Repository:
    conn = connect(":memory:")
    migrate(conn)
    r = Repository(conn)
    r.set_state("kill_switch", False)
    r.set_state("last_feed_ts", NOW_TS)
    return r


def test_a_placed_order_records_one_decision(repo, broker, config, signal) -> None:
    result = executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)

    decisions = repo.get_order_decisions()
    assert len(decisions) == 1
    assert decisions[0]["disposition"] == "placed"
    assert decisions[0]["order_id"] == result.order_id
    assert decisions[0]["vetoed_by"] is None


def test_a_vetoed_intent_records_a_decision_with_no_order_id(repo, broker, config) -> None:
    """The row exists precisely BECAUSE no order does -- this is the refusal record."""
    from keel.strategy.rules.base import Action, Setup, Signal
    from keel.types import Side

    oversized = Signal(
        rule_name="turtle_breakout",
        product_id="DOGE-USD",          # not on the allowlist -> rail 1 vetoes
        action=Action.ENTER,
        side=Side.BUY,
        setup=Setup(
            product_id="DOGE-USD", direction="long", entry=Decimal("1"),
            stop=Decimal("0.9"), target=Decimal("1.3"), context={"adx": "31"},
        ),
        cts_score=7,
        entry_technique="limit",
        ts=NOW_TS,
    )

    result = executor.execute(oversized, broker, repo, config, "bypass", now_ts=NOW_TS)

    assert result.placed is False
    decisions = repo.get_order_decisions()
    assert len(decisions) == 1
    assert decisions[0]["disposition"] == "vetoed"
    assert decisions[0]["order_id"] is None
    assert "halal_allowlist" in decisions[0]["vetoed_by"]


def test_confirm_mode_without_a_callback_records_not_placed(repo, broker, config, signal) -> None:
    """Distinguishable from 'vetoed' -- the rails allowed it, the confirm gate did not."""
    result = executor.execute(signal, broker, repo, config, "confirm", now_ts=NOW_TS)

    assert result.placed is False
    decision = repo.get_order_decisions()[0]
    assert decision["disposition"] == "not_placed"
    assert decision["vetoed_by"] is None
    assert "confirm" in decision["reason"]


def test_the_indicator_snapshot_is_persisted(repo, broker, config, signal) -> None:
    executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)
    assert repo.get_order_decisions()[0]["context"] == signal.setup.context


def test_cts_score_and_entry_technique_are_persisted(repo, broker, config, signal) -> None:
    executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)
    decision = repo.get_order_decisions()[0]
    assert decision["cts_score"] == signal.cts_score
    assert decision["entry_technique"] == signal.entry_technique


def test_a_dca_decision_is_recorded_like_any_other(repo, broker, config, dca_signal) -> None:
    """This is attribution, not a rail -- the rail 8/11/16 DCA exemptions do not apply here."""
    executor.execute(dca_signal, broker, repo, config, "bypass", now_ts=NOW_TS)
    decisions = repo.get_order_decisions()
    assert len(decisions) == 1
    assert decisions[0]["is_dca"] is True


def test_a_no_op_sell_records_nothing(repo, broker, config) -> None:
    """THE SCOPE BOUNDARY. Nothing held -> `execute` returns before guards.check, so no decision
    was made and none must be recorded. Without this, the table fills with non-events."""
    from keel.strategy.rules.base import Action, Signal
    from keel.types import Side

    nothing_to_sell = Signal(
        rule_name="turtle_breakout", product_id="BTC-USD", action=Action.EXIT,
        side=Side.SELL, setup=None, cts_score=0, entry_technique="market", ts=NOW_TS,
    )

    executor.execute(nothing_to_sell, broker, repo, config, "bypass", now_ts=NOW_TS)

    assert repo.get_order_decisions() == []


def test_exactly_one_row_per_call_not_two(repo, broker, config, signal) -> None:
    """The wrapper records once. A second write would double-count every trade in evaluation."""
    executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)
    assert len(repo.get_order_decisions()) == 1
```

**Import the existing builders — do not write new ones.** `tests/execution/test_executor.py` already
provides everything these tests need: `FakeBroker` (line 50), the `repo` fixture (line 155), `_config`
(167), `_setup` (187), `_enter_signal` (201) and `_dca_signal` (218). Add this to the top of the new
file and define the four fixtures over them:

```python
from tests.execution.test_executor import (
    FakeBroker,
    _config as _executor_config,
    _dca_signal,
    _enter_signal,
)


@pytest.fixture
def repo() -> Repository:
    return _repo()


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker()


@pytest.fixture
def config():
    return _executor_config()


@pytest.fixture
def signal():
    return _enter_signal()


@pytest.fixture
def dca_signal():
    return _dca_signal()
```

Note `test_executor.py`'s own `repo` fixture attests a large subscription (rail 14); the local
`_repo()` above deliberately does not, so seed whatever a given test needs. If a test trips rail 14
rather than the rail it targets, that is why.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/execution/test_order_decisions.py -v`
Expected: FAIL — `get_order_decisions()` returns `[]` because nothing writes decisions yet.

- [ ] **Step 3: Rename the body to a private inner function**

In `keel/execution/executor.py`, rename the existing `def execute(` to:

```python
def _execute_inner(
    signal: Signal,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: Literal["confirm", "bypass"],
    confirm_fn: ConfirmFn | None = None,
    now_ts: int | None = None,
) -> tuple[ExecutionResult, OrderIntent | None]:
```

Change **every** `return ExecutionResult(...)` inside it to `return ExecutionResult(...), intent`,
except the early no-op-SELL return before the intent exists, which becomes
`return ExecutionResult(...), None`.

Run `uv run grep -n "return ExecutionResult" keel/execution/executor.py` and confirm every return
inside `_execute_inner` now yields a 2-tuple. A missed one is a `TypeError` at runtime, not a silent
failure — but check anyway.

- [ ] **Step 4: Add the recording wrapper**

Add immediately after `_execute_inner`:

```python
def _disposition(result: ExecutionResult) -> str:
    """Map an `ExecutionResult` onto the three recorded outcomes.

    `not_placed` deliberately covers two causes -- a confirm-gate refusal and a broker rejection --
    which `reason` (stored verbatim) disambiguates. Evaluation groups by disposition; audit reads
    reason.
    """
    if result.vetoed_by:
        return "vetoed"
    if result.placed:
        return "placed"
    return "not_placed"


def execute(
    signal: Signal,
    broker: Any,
    repo: Repository,
    config: Config,
    mode: Literal["confirm", "bypass"],
    confirm_fn: ConfirmFn | None = None,
    now_ts: int | None = None,
) -> ExecutionResult:
    """Execute `signal`, recording exactly one `order_decisions` row for the attempt.

    The recording lives in this wrapper rather than at each `return` inside `_execute_inner`
    because that function has four return paths; writing at each would make a future fifth
    silently unrecorded. A decision that never reached `guards.check` (a no-op SELL) yields
    `intent is None` and is correctly not recorded -- no decision was made.
    """
    result, intent = _execute_inner(
        signal, broker, repo, config, mode, confirm_fn=confirm_fn, now_ts=now_ts
    )
    if intent is None:
        return result

    ts = now_ts if now_ts is not None else int(time.time())
    setup = signal.setup
    decision_id = repo.insert_order_decision(
        {
            "ts": ts,
            "product_id": intent.product_id,
            "side": intent.side.value if isinstance(intent.side, Side) else intent.side,
            "order_id": result.order_id,
            "rule_name": signal.rule_name,
            "cts_score": signal.cts_score,
            "entry_technique": signal.entry_technique,
            "entry": None if setup is None else setup.entry,
            "stop": None if setup is None else setup.stop,
            "target": None if setup is None else setup.target,
            "notional": intent.notional,
            "is_dca": intent.is_dca,
            "disposition": _disposition(result),
            "reason": result.reason,
            "vetoed_by": result.vetoed_by or None,
            "context": {} if setup is None else setup.context,
        }
    )

    # Compact operational event only -- the indicator snapshot stays in the table. Monorepo spec
    # §10.3: telemetry is not the audit trail, and the same rationale in two places that can
    # disagree once the log rotates is exactly what it warns against.
    log_event(
        logger,
        logging.INFO,
        f"execution.decision_{_disposition(result)}",
        decision_id=decision_id,
        rule=signal.rule_name,
        product=intent.product_id,
        cts_score=signal.cts_score,
        vetoed_by=result.vetoed_by or None,
    )
    return result
```

Ensure `time` is imported in this module.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/execution/test_order_decisions.py -v`
Expected: 8 passed.

- [ ] **Step 6: Confirm no existing caller broke**

Run: `uv run pytest tests/execution/ tests/test_agent.py tests/test_cli.py -q`
Expected: all pass. `execute()`'s public signature and return type are unchanged, so callers in
`agent.py`, `scale_out`, `handle_oco_fill`, `roll_to_break_even` and `trail_stop_atr` are unaffected —
but they now produce decision rows too, which is intended.

- [ ] **Step 7: Prove the scope boundary discriminates (mutation check)**

In a scratch worktree, change the wrapper's `if intent is None: return result` to record anyway
(passing `intent=None` guarded fields as `None`). Re-run
`uv run pytest tests/execution/test_order_decisions.py -k no_op_sell -q`.
Expected: `test_a_no_op_sell_records_nothing` **FAILS**. If it passes, the boundary is untested and
the table will accumulate non-events.

- [ ] **Step 8: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: record one order decision per execute() call"
```

---

### Task 3: Populate `orders.rule_id` for live orders

`_order_row` hardcodes `rule_id=None`, so live orders carry no attribution while paper mode smuggles the rule name into `raw_response`. The decision record is the rich account; `orders.rule_id` is the cheap join key.

**Files:**
- Modify: `keel/data/repository.py` (new lookup)
- Modify: `keel/execution/executor.py` (`_order_row`, and its call site)
- Test: `tests/execution/test_order_decisions.py`, `tests/data/test_repository.py`

**Interfaces:**
- Consumes: nothing from Task 2.
- Produces: `Repository.get_rule_id_by_name(name: str) -> int | None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/data/test_repository.py`:

```python
def test_get_rule_id_by_name_finds_a_seeded_rule() -> None:
    repo = _repo()
    rule_id = repo.insert_rule("turtle_breakout", {}, status="live")
    assert repo.get_rule_id_by_name("turtle_breakout") == rule_id


def test_get_rule_id_by_name_returns_none_for_an_unknown_rule() -> None:
    """The negative: an unregistered name must yield None so the order records NULL rather than
    a guessed id. A wrong rule_id is worse than no rule_id -- it misattributes real money."""
    assert _repo().get_rule_id_by_name("no_such_rule") is None
```

`insert_rule`'s real signature is `insert_rule(kind: str, params: dict, status: str = "candidate", now_ts: int | None = None) -> int` (`repository.py:284`) -- positional, NOT a dict. Use that file's existing `_repo()` helper.

Add to `tests/execution/test_order_decisions.py`:

```python
def test_a_placed_live_order_records_its_rule_id(repo, broker, config, signal) -> None:
    rule_id = repo.insert_rule(signal.rule_name, {}, status="live")
    result = executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)

    order = repo.get_order(result.order_id)
    assert order["rule_id"] == rule_id


def test_an_order_from_an_unregistered_rule_records_a_null_rule_id(
    repo, broker, config, signal
) -> None:
    """No rules row seeded -- the order must store NULL, never a fabricated id."""
    result = executor.execute(signal, broker, repo, config, "bypass", now_ts=NOW_TS)
    assert repo.get_order(result.order_id)["rule_id"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/data/test_repository.py -k rule_id_by_name tests/execution/test_order_decisions.py -k rule_id -v`
Expected: FAIL — `AttributeError: 'Repository' object has no attribute 'get_rule_id_by_name'`

- [ ] **Step 3: Add the lookup**

In `keel/data/repository.py`:

```python
    def get_rule_id_by_name(self, name: str) -> int | None:
        """The `rules.id` whose `kind` matches `name`, or None if the rule is not registered.

        Returns None rather than raising: an unregistered rule must record a NULL `rule_id`, never
        a guessed one. A wrong attribution on a live order is worse than a missing one, because it
        silently credits real money to the wrong strategy.
        """
        row = self._conn.execute(
            "SELECT id FROM rules WHERE kind = ? ORDER BY id LIMIT 1", (name,)
        ).fetchone()
        return None if row is None else int(row["id"])
```

- [ ] **Step 4: Thread it into the order row**

In `keel/execution/executor.py`, change `_order_row`'s signature and the hardcoded field:

```python
def _order_row(
    intent: OrderIntent, mode: str, now_ts: int, rule_id: int | None = None
) -> dict[str, Any]:
    return dict(
        ...
        rule_id=rule_id,
        ...
    )
```

and its call site inside `_execute_inner`:

```python
    order_id = repo.insert_order(
        _order_row(intent, mode, now_ts, rule_id=repo.get_rule_id_by_name(signal.rule_name))
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/data/test_repository.py tests/execution/test_order_decisions.py -v`
Expected: all pass.

- [ ] **Step 6: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: populate orders.rule_id for live orders"
```

---

### Task 4: `render_rationale` — the derived human sentence

**Files:**
- Create: `packages/keel-core/keel_core/rationale.py`
- Test: `tests/test_rationale.py`

**Interfaces:**
- Consumes: a decision dict shaped like `Repository.get_order_decisions()`'s rows (Task 1).
- Produces: `keel_core.rationale.render_rationale(decision: dict) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rationale.py`:

```python
"""The human sentence is DERIVED from stored fields, never persisted.

Stored prose drifts from the data it describes the moment either changes -- so this is a pure
function over a decision row, and there is no rationale column to disagree with it.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.rationale import render_rationale


def _decision(**overrides: object) -> dict:
    base: dict = {
        "product_id": "BTC-USD",
        "side": "BUY",
        "rule_name": "turtle_breakout",
        "cts_score": 8,
        "entry_technique": "limit",
        "entry": Decimal("50000"),
        "stop": Decimal("49000"),
        "target": Decimal("53000"),
        "disposition": "placed",
        "reason": "placed",
        "vetoed_by": None,
        "context": {"adx": "31.2", "donchian_high": "49800"},
    }
    base.update(overrides)
    return base


def test_a_placed_decision_names_rule_product_and_levels() -> None:
    sentence = render_rationale(_decision())
    assert "turtle_breakout" in sentence
    assert "BTC-USD" in sentence
    assert "50000" in sentence
    assert "49000" in sentence
    assert "8" in sentence


def test_a_vetoed_decision_names_the_rails_that_refused_it() -> None:
    sentence = render_rationale(
        _decision(disposition="vetoed", vetoed_by=["per_order_cap", "stale_data"])
    )
    assert "per_order_cap" in sentence
    assert "stale_data" in sentence


def test_a_placed_decision_does_not_claim_a_veto() -> None:
    """The negative control -- a placed trade must not read as refused."""
    sentence = render_rationale(_decision())
    assert "vetoed" not in sentence.lower()
    assert "refused" not in sentence.lower()


def test_the_context_snapshot_appears_in_the_sentence() -> None:
    sentence = render_rationale(_decision(context={"adx": "31.2"}))
    assert "adx" in sentence
    assert "31.2" in sentence


def test_an_exit_decision_without_a_setup_still_renders() -> None:
    """EXIT signals carry no setup, so entry/stop/target/cts are None -- must not crash."""
    sentence = render_rationale(
        _decision(side="SELL", entry=None, stop=None, target=None,
                  cts_score=None, entry_technique=None, context={})
    )
    assert "turtle_breakout" in sentence
    assert "BTC-USD" in sentence


def test_it_is_pure_and_stdlib_only() -> None:
    """keel_core must stay dependency-free -- see the Global Constraints."""
    import keel_core.rationale as mod

    source = open(mod.__file__).read()
    assert "import requests" not in source
    assert "from keel." not in source   # no dependency back on the app package
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_rationale.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel_core.rationale'`

- [ ] **Step 3: Implement it**

Create `packages/keel-core/keel_core/rationale.py`:

```python
"""Render a stored order decision as a human sentence.

DERIVED, never stored. Prose persisted alongside the fields it describes drifts from them the
moment either changes, so the sentence is rebuilt at read time from the decision row and there is
no rationale column that could disagree with it.

Pure and stdlib-only: no I/O, no dependency on the `keel` application package.
"""

from __future__ import annotations

from typing import Any

__all__ = ["render_rationale"]


def _levels(decision: dict[str, Any]) -> str:
    parts = []
    if decision.get("entry") is not None:
        parts.append(f"at {decision['entry']}")
    if decision.get("stop") is not None:
        parts.append(f"stop {decision['stop']}")
    if decision.get("target") is not None:
        parts.append(f"target {decision['target']}")
    if decision.get("cts_score") is not None:
        parts.append(f"CTS {decision['cts_score']}")
    if decision.get("entry_technique"):
        parts.append(f"entry: {decision['entry_technique']}")
    return f" ({', '.join(parts)})" if parts else ""


def _evidence(decision: dict[str, Any]) -> str:
    context = decision.get("context") or {}
    if not context:
        return ""
    shown = ", ".join(f"{k}={v}" for k, v in sorted(context.items()))
    return f" — {shown}"


def render_rationale(decision: dict[str, Any]) -> str:
    """One sentence explaining what was decided and why."""
    action = {"placed": "placed", "vetoed": "REFUSED", "not_placed": "not placed"}.get(
        decision.get("disposition", ""), decision.get("disposition", "")
    )
    head = (
        f"{decision['rule_name']} {action} {decision['side']} {decision['product_id']}"
        f"{_levels(decision)}"
    )
    if decision.get("vetoed_by"):
        head += f" — blocked by {', '.join(decision['vetoed_by'])}"
    return head + _evidence(decision)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_rationale.py -v`
Expected: 6 passed.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Run: `uv run pytest tests/baseline/ -v && git diff --stat tests/fixtures/baseline_backtest.json`
Expected: all pass; no diff output.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: render_rationale, derived from stored decision fields"
```

---

## Done criteria

- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` all pass; baseline fixture byte-identical.
- Every intent reaching `guards.check` produces **exactly one** `order_decisions` row.
- A no-op SELL that returns before `guards.check` produces **none** — proven by mutation.
- A vetoed decision stores `order_id IS NULL` and the exact rail names from `GuardResult.violations`.
- `placed`, `vetoed` and `not_placed` are all distinguishable, with `reason` disambiguating the two causes of `not_placed`.
- The indicator snapshot round-trips exactly and is queryable by `rule_name`.
- Live orders store a real `orders.rule_id`, or NULL for an unregistered rule — never a guessed one.
- One compact log event per decision; the snapshot appears in the DB only.
- `render_rationale` is pure, lives in `keel_core`, and no prose column exists.

## Follow-on (not in this plan)

- **A CLI or report over `order_decisions`.** The data lands first; surfacing it (e.g. `keel decisions --rule turtle_breakout`) is separate work.
- **Joining decisions to `trade_outcomes`** to answer "which rationale produced which P&L" — the payoff of both plans together, and the actual point of "continuous evaluation".
- **Retention.** The table grows unbounded, though only when a rule fires. Named, not solved.
