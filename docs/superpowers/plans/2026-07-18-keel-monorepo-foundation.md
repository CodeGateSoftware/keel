# keel Monorepo Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the `uv` workspace, extract `keel-core`, and convert logging to structured JSON events — guarded by a backtest-output regression baseline that proves no strategy behaviour changed.

**Architecture:** Implements §12 steps 1–2 of `docs/superpowers/specs/2026-07-18-keel-monorepo-architecture-design.md`. Task 1 builds the regression net that every later step in the migration depends on. Task 2 converts the repository into a `uv` workspace and moves `types.py`/`config.py`/`logging_setup.py` into `packages/keel-core`. Tasks 3–4 add `keel_core.telemetry` and migrate the 36 existing `logger.*` call sites to structured events carrying a `cycle_id`.

**Tech Stack:** Python 3.12, `uv` + `uv_build` workspaces, `pytest`, `ruff`, stdlib `logging` + `json`.

## Global Constraints

- Python `>=3.12`; `line-length = 100`; ruff `select = ["E", "F", "I", "UP"]`, `ignore = ["UP042"]`.
- **No strategy behaviour may change in this plan.** The Task 1 baseline test must pass unmodified at the end of every subsequent task.
- Package distribution names use hyphens (`keel-core`); import names use underscores (`keel_core`).
- `keel-core` takes **no third-party dependencies beyond `pyyaml` and `python-dotenv`** —
  `config.py` needs both (`yaml.safe_load` for `config.yaml`, `dotenv_values` at `config.py:464`
  for `load_secrets`). The intent is to keep broker SDKs and other heavy deps out of core.
- Log `event` values are stable identifiers (`agent.cycle_start`), never interpolated sentences.
- Existing behaviour of `LoggingConfig` is preserved: `verbose=False` → `ERROR` level, `verbose=True` → `INFO`.
- Commit after every task. Never commit `keel.db`, `*.log`, or `transactions/` (all gitignored).

---

### Task 1: Backtest regression baseline

The migration's core risk is silently changing strategy output while moving files. Nothing currently proves that didn't happen. This task builds a golden-file test over real historical candles: it runs `backtest()` and byte-compares a canonical serialisation of the result against a committed fixture.

`BTC-USD`/`ONE_DAY` is chosen because `TurtleBreakout` trades daily bars only, and 1819 rows make a reasonably sized committed fixture. OHLCV are stored as exact `TEXT` Decimals, so the comparison is exact rather than float-tolerant.

**Files:**
- Create: `tests/baseline/__init__.py`
- Create: `tests/baseline/export_baseline.py`
- Create: `tests/baseline/serialize.py`
- Create: `tests/baseline/regenerate_golden.py`
- Create: `tests/baseline/test_backtest_baseline.py`
- Create (generated, committed): `tests/fixtures/baseline_candles.json`
- Create (generated, committed): `tests/fixtures/baseline_backtest.json`

**Design constraint:** the test is **read-only**. Regeneration lives in the two scripts, never in
the test. This baseline is the only thing proving the migration does not change strategy output,
so a test that can overwrite its own expected values could silently launder a real behaviour
change into the fixture, after which every later task validates against corrupted truth.

**Interfaces:**
- Consumes: `keel.strategy.backtest.backtest(rule, candles) -> BacktestResult`; `keel.strategy.rules.turtle_breakout.TurtleBreakout(product_id=...)`; `keel.types.Candle(ts, open, high, low, close, volume)`.
- Produces: `tests/baseline/serialize.py::serialize_result(result) -> dict`, `load_baseline_candles() -> list[Candle]`. Later tasks rely on `pytest tests/baseline/ -v` passing unchanged.

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p tests/baseline
touch tests/baseline/__init__.py
```

- [ ] **Step 2: Write the candle exporter**

Create `tests/baseline/export_baseline.py`:

```python
"""Dev script: export a fixed candle slice from a local keel.db into a committed fixture.

`keel.db` is gitignored (it holds personal trading data), so the regression baseline cannot
read it at test time. Run this manually only when the baseline corpus must be regenerated:

    uv run python tests/baseline/export_baseline.py --db keel.db

Candle OHLCV are stored as exact decimal TEXT, so this round-trips without precision loss.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

PRODUCT = "BTC-USD"
GRANULARITY = "ONE_DAY"
OUT = Path(__file__).parent.parent / "fixtures" / "baseline_candles.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="keel.db")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT ts, o, h, l, c, v FROM candles "
        "WHERE product_id = ? AND granularity = ? ORDER BY ts ASC",
        (PRODUCT, GRANULARITY),
    ).fetchall()
    conn.close()

    if not rows:
        raise SystemExit(f"no candles for {PRODUCT}/{GRANULARITY} in {args.db}")

    payload = {
        "product_id": PRODUCT,
        "granularity": GRANULARITY,
        "candles": [
            {
                "ts": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
            for row in rows
        ],
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {len(rows)} candles to {OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Generate the candle fixture**

Run: `uv run python tests/baseline/export_baseline.py --db keel.db`
Expected: `wrote 1819 candles to .../tests/fixtures/baseline_candles.json`

If the count differs, that is fine — the DB has moved on since the plan was written. Record the actual count; it only has to be stable from here on.

- [ ] **Step 4: Write the serializer**

Create `tests/baseline/serialize.py`:

```python
"""Canonical, order-stable serialisation of a `BacktestResult` for golden-file comparison.

Decimals become strings so the comparison is exact rather than float-tolerant: a refactor that
perturbs arithmetic by one ulp is a behaviour change and must fail this test.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.strategy.stats import BacktestResult
from keel.types import Candle

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_baseline_candles() -> list[Candle]:
    """Load the committed baseline candle corpus."""
    payload = json.loads((FIXTURES / "baseline_candles.json").read_text())
    return [
        Candle(
            ts=row["ts"],
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        for row in payload["candles"]
    ]


def serialize_result(result: BacktestResult) -> dict[str, Any]:
    """Render `result` as JSON-safe primitives, Decimals as exact strings."""

    def dec(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    return {
        "n_trades": result.n_trades,
        "win_rate": result.win_rate,
        "avg_win": dec(result.avg_win),
        "avg_loss": dec(result.avg_loss),
        "expectancy": dec(result.expectancy),
        "profit_factor": dec(result.profit_factor),
        "max_drawdown": dec(result.max_drawdown),
        "max_losing_streak": result.max_losing_streak,
        "avg_mfe": dec(result.avg_mfe),
        "avg_mae": dec(result.avg_mae),
        "trades": [
            {
                "entry_ts": t.entry_ts,
                "exit_ts": t.exit_ts,
                "entry": dec(t.entry),
                "exit": dec(t.exit),
                "qty": dec(t.qty),
                "side": t.side.value if hasattr(t.side, "value") else str(t.side),
                "pnl": dec(t.pnl),
                "r_multiple": dec(t.r_multiple),
                "mfe": dec(t.mfe),
                "mae": dec(t.mae),
                "outcome": t.outcome,
            }
            for t in result.trades
        ],
    }
```

- [ ] **Step 5: Write the shared backtest builder and the golden regeneration script**

Both the test and the regeneration script must run the *identical* backtest, or the golden file
would not describe what the test checks. Put the construction in one place.

Append to `tests/baseline/serialize.py`:

```python
def run_baseline_backtest() -> dict[str, Any]:
    """The one canonical baseline backtest, shared by the test and the regeneration script."""
    from keel.strategy.backtest import backtest
    from keel.strategy.rules.turtle_breakout import TurtleBreakout

    rule = TurtleBreakout(product_id="BTC-USD")
    return serialize_result(backtest(rule, load_baseline_candles()))


GOLDEN = FIXTURES / "baseline_backtest.json"
```

Create `tests/baseline/regenerate_golden.py`:

```python
"""Dev script: regenerate the committed backtest golden file.

Deliberately separate from the test, which is read-only. This baseline is the only thing
proving the monorepo migration does not change strategy output, so a test able to rewrite its
own expected values could silently launder a real behaviour change into the fixture.

Run this only when a strategy change is intended, and review the resulting diff:

    uv run python tests/baseline/regenerate_golden.py

Needs no database -- it reads the committed candle fixture.
"""

from __future__ import annotations

import json

from tests.baseline.serialize import GOLDEN, run_baseline_backtest


def main() -> None:
    payload = run_baseline_backtest()
    GOLDEN.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {GOLDEN} ({payload['n_trades']} trades)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Write the read-only golden test**

Create `tests/baseline/test_backtest_baseline.py`:

```python
"""Regression baseline: `backtest()` output must not change during the monorepo migration.

Read-only by design. Regenerate deliberately with:

    uv run python tests/baseline/regenerate_golden.py
"""

from __future__ import annotations

import json

from tests.baseline.serialize import GOLDEN, load_baseline_candles, run_baseline_backtest


def test_turtle_breakout_backtest_matches_baseline() -> None:
    assert run_baseline_backtest() == json.loads(GOLDEN.read_text())


def test_baseline_corpus_is_non_trivial() -> None:
    """Guard against an empty fixture silently making the golden test vacuous."""
    assert len(load_baseline_candles()) > 1000


def test_baseline_records_trades() -> None:
    """A zero-trade baseline would pass forever while proving nothing."""
    assert json.loads(GOLDEN.read_text())["n_trades"] > 0
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `uv run pytest tests/baseline/ -v`
Expected: FAIL — `FileNotFoundError: .../tests/fixtures/baseline_backtest.json`

- [ ] **Step 8: Generate the golden file**

Run: `uv run python tests/baseline/regenerate_golden.py`
Expected: `wrote .../tests/fixtures/baseline_backtest.json (N trades)` with N greater than zero.

If N is zero, stop — the rule found no trades over this corpus and the baseline would be
vacuous. Report it rather than proceeding.

- [ ] **Step 9: Verify the test passes against the generated golden**

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed

- [ ] **Step 10: Verify the full suite still passes**

Run: `uv run pytest -q`
Expected: 720 passed (717 existing + 3 new)

- [ ] **Step 11: Commit**

```bash
git add tests/baseline tests/fixtures/baseline_candles.json tests/fixtures/baseline_backtest.json
git commit -m "test: backtest regression baseline for the monorepo migration"
```

---

### Task 2: uv workspace and the `keel-core` package

Converts the repository into a `uv` workspace and moves the three dependency-free modules into `packages/keel-core`. `keel` keeps working unchanged — `keel.types` and `keel.config` become re-export shims so no call site moves in this task. Deferring the call-site rewrite keeps this task's diff mechanical and reviewable.

`halal_cb/` is deleted here: it is untracked (only gitignored `__pycache__` survives the rename), so this is a filesystem removal with no git involvement.

**Files:**
- Delete: `halal_cb/`
- Create: `packages/keel-core/pyproject.toml`
- Create: `packages/keel-core/keel_core/__init__.py`
- Create: `packages/keel-core/keel_core/types.py` (moved from `keel/types.py`)
- Create: `packages/keel-core/keel_core/config.py` (moved from `keel/config.py`)
- Create: `packages/keel-core/keel_core/logging_setup.py` (moved from `keel/logging_setup.py`)
- Modify: `keel/types.py`, `keel/config.py`, `keel/logging_setup.py` → re-export shims
- Modify: `pyproject.toml` (workspace root)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: importable `keel_core.types`, `keel_core.config`, `keel_core.logging_setup`. `keel.types` etc. remain importable and re-export identical objects. Task 3 adds `keel_core/telemetry.py` alongside these.

- [ ] **Step 1: Remove the dead package**

```bash
rm -rf halal_cb
```

Run: `ls halal_cb 2>&1`
Expected: `ls: halal_cb: No such file or directory`

- [ ] **Step 2: Create the `keel-core` package skeleton**

```bash
mkdir -p packages/keel-core/keel_core
```

Create `packages/keel-core/pyproject.toml`:

```toml
[project]
name = "keel-core"
version = "0.1.0"
description = "Shared domain types, configuration, and logging for keel"
requires-python = ">=3.12"
dependencies = ["pyyaml>=6.0.3"]

[build-system]
requires = ["uv_build>=0.10.4,<0.11.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-root = ""
```

`pyyaml` is included because `config.py` parses `config.yaml`. It is the one third-party dependency the package genuinely needs; nothing else may be added.

Create `packages/keel-core/keel_core/__init__.py`:

```python
"""Shared domain types, configuration, and logging for every keel package and app."""
```

- [ ] **Step 3: Move the three modules**

```bash
git mv keel/types.py packages/keel-core/keel_core/types.py
git mv keel/config.py packages/keel-core/keel_core/config.py
git mv keel/logging_setup.py packages/keel-core/keel_core/logging_setup.py
```

- [ ] **Step 4: Fix the moved modules' internal imports**

`logging_setup.py` imports `from keel.config import LoggingConfig`. Rewrite it to the new package:

```python
from keel_core.config import LoggingConfig
```

Run: `grep -rn "from keel\.\|import keel\b" packages/keel-core/`
Expected: no output. If any line appears, rewrite it to `keel_core.` — `keel-core` must not depend on `keel`.

- [ ] **Step 5: Add the re-export shims**

Create `keel/types.py`:

```python
"""Compatibility shim: `keel.types` now lives in `keel_core.types`.

Retained so this task's diff stays mechanical; call sites migrate in a later task.
"""

from keel_core.types import *  # noqa: F403
from keel_core.types import __all__  # noqa: F401
```

Create `keel/config.py`:

```python
"""Compatibility shim: `keel.config` now lives in `keel_core.config`."""

from keel_core.config import *  # noqa: F403
from keel_core.config import __all__  # noqa: F401
```

Create `keel/logging_setup.py`:

```python
"""Compatibility shim: `keel.logging_setup` now lives in `keel_core.logging_setup`."""

from keel_core.logging_setup import *  # noqa: F403
from keel_core.logging_setup import __all__  # noqa: F401
```

- [ ] **Step 6: Add `__all__` where the moved modules lack it**

The shims import `__all__`, so each moved module must define one. Check:

Run: `grep -c "^__all__" packages/keel-core/keel_core/types.py packages/keel-core/keel_core/config.py packages/keel-core/keel_core/logging_setup.py`

For any file reporting `0`, append an `__all__` listing its public names. Derive the list with:

```bash
uv run python -c "
import ast,sys
for p in ['types','config','logging_setup']:
    src=open(f'packages/keel-core/keel_core/{p}.py').read()
    names=[n.name for n in ast.parse(src).body if isinstance(n,(ast.ClassDef,ast.FunctionDef))]
    print(p, [x for x in names if not x.startswith('_')])
"
```

Append the printed list to each file as `__all__ = [...]`.

- [ ] **Step 7: Convert the root into a workspace**

Modify `pyproject.toml` — add the workspace table and depend on `keel-core`:

```toml
[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
keel-core = { workspace = true }
```

And add `"keel-core"` to the existing `[project] dependencies` list.

- [ ] **Step 8: Re-resolve the workspace**

Run: `uv sync`
Expected: resolves without error; `keel-core` appears as a workspace member.

Run: `uv run python -c "import keel_core.types, keel.types; print(keel.types.Candle is keel_core.types.Candle)"`
Expected: `True`

- [ ] **Step 9: Verify the baseline and full suite**

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed — **the golden file must match byte-for-byte.** A failure here means the move changed behaviour; stop and investigate rather than regenerating the baseline.

Run: `uv run pytest -q && uv run ruff check .`
Expected: 720 passed; ruff clean.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: uv workspace + extract keel-core package"
```

---

### Task 3: Structured JSON telemetry

Replaces the plaintext log format with JSON lines carrying stable fields. Per spec §10.2 this is the retrofit-expensive change, so it lands before any app split. `configure_logging`'s existing level semantics are preserved exactly; only the formatter and a new emit helper are added.

**Files:**
- Create: `packages/keel-core/keel_core/telemetry.py`
- Create: `tests/test_telemetry.py`
- Modify: `packages/keel-core/keel_core/logging_setup.py`
- Modify: `tests/test_logging_setup.py`

**Interfaces:**
- Consumes: `keel_core.config.LoggingConfig` from Task 2.
- Produces: `keel_core.telemetry.JsonFormatter`, `log_event(logger, level, event, **fields) -> None`, `bind_cycle(cycle_id: str | None) -> None`, `current_cycle() -> str | None`, `new_cycle_id() -> str`. Task 4 calls `log_event` and `bind_cycle`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telemetry.py`:

```python
from __future__ import annotations

import json
import logging

from keel_core import telemetry


def _capture(caplog, fn) -> dict:
    """Run `fn`, formatting the single emitted record through JsonFormatter."""
    formatter = telemetry.JsonFormatter()
    with caplog.at_level(logging.INFO, logger="keel.test"):
        fn(logging.getLogger("keel.test"))
    assert len(caplog.records) == 1
    return json.loads(formatter.format(caplog.records[0]))


def test_log_event_emits_stable_fields(caplog) -> None:
    payload = _capture(
        caplog,
        lambda log: telemetry.log_event(
            log, logging.INFO, "agent.cycle_start", product="BTC-USD", venue="coinbase"
        ),
    )
    assert payload["event"] == "agent.cycle_start"
    assert payload["product"] == "BTC-USD"
    assert payload["venue"] == "coinbase"
    assert payload["level"] == "INFO"
    assert "ts" in payload


def test_cycle_id_is_attached_when_bound(caplog) -> None:
    telemetry.bind_cycle("cycle-abc")
    try:
        payload = _capture(
            caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
        )
        assert payload["cycle_id"] == "cycle-abc"
    finally:
        telemetry.bind_cycle(None)


def test_cycle_id_absent_when_unbound(caplog) -> None:
    telemetry.bind_cycle(None)
    payload = _capture(
        caplog, lambda log: telemetry.log_event(log, logging.INFO, "agent.cycle_start")
    )
    assert payload.get("cycle_id") is None


def test_output_is_one_json_object_per_line(caplog) -> None:
    payload = _capture(
        caplog, lambda log: telemetry.log_event(log, logging.ERROR, "executor.order_rejected")
    )
    assert "\n" not in json.dumps(payload)


def test_new_cycle_id_is_unique() -> None:
    assert telemetry.new_cycle_id() != telemetry.new_cycle_id()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel_core.telemetry'`

- [ ] **Step 3: Implement the telemetry module**

Create `packages/keel-core/keel_core/telemetry.py`:

```python
"""Structured event logging shared by every keel package and app.

Log records are emitted as one JSON object per line with a stable field set, so that events can
be grouped and queried across processes once engine/ingest/sim run separately. `event` is a
stable identifier (`agent.cycle_start`), never an interpolated sentence -- interpolated messages
cannot be aggregated, and fixing that later means rewriting every call site.

`cycle_id` correlates every event emitted during one engine loop. Once apps are separate
processes it becomes the trace ID, so it is carried in a `ContextVar` rather than threaded
through call signatures.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar
from typing import Any

_cycle_id: ContextVar[str | None] = ContextVar("keel_cycle_id", default=None)

# Attribute name under which `log_event` stashes structured fields on a LogRecord.
_FIELDS_ATTR = "keel_fields"

# Payload keys `JsonFormatter` owns. A caller field with one of these names is emitted under a
# `field_`-prefixed key rather than overwriting the stable one -- never dropped, never raised.
_RESERVED = frozenset({"ts", "level", "logger", "event", "cycle_id", "exc"})


def new_cycle_id() -> str:
    """Generate a fresh correlation id for one engine cycle."""
    return uuid.uuid4().hex[:16]


def bind_cycle(cycle_id: str | None) -> None:
    """Bind (or clear, with `None`) the cycle id attached to subsequent events."""
    _cycle_id.set(cycle_id)


def current_cycle() -> str | None:
    """The currently bound cycle id, if any."""
    return _cycle_id.get()


def log_event(logger: logging.Logger, level: int, event: str, /, **fields: Any) -> None:
    """Emit a structured `event` with arbitrary `fields` attached.

    `event` must be a stable identifier. `fields` values must be JSON-serialisable or have a
    useful `str()` -- `JsonFormatter` falls back to `str()` rather than raising, because a
    logging call must never take down the trade loop.
    """
    logger.log(level, event, extra={_FIELDS_ATTR: fields})


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }

        cycle = _cycle_id.get()
        if cycle is not None:
            payload["cycle_id"] = cycle

        fields = getattr(record, _FIELDS_ATTR, None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in _RESERVED:
                    payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: 5 passed

- [ ] **Step 5: Wire the formatter into `configure_logging`**

Modify `packages/keel-core/keel_core/logging_setup.py` — replace the `_FORMAT` constant and the `setFormatter` call.

Delete:

```python
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
```

Replace `handler.setFormatter(logging.Formatter(_FORMAT))` with:

```python
handler.setFormatter(JsonFormatter())
```

Add the import at the top of the file:

```python
from keel_core.telemetry import JsonFormatter
```

Update the module docstring's format description: log lines are now single-line JSON objects, not `%(asctime)s ...` text. Leave the level and rotation paragraphs unchanged — that behaviour is unaffected.

- [ ] **Step 6: Tighten the logging-setup tests**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: **all pass, unchanged.**

This is the point of the step. The four content assertions (`test_logging_setup.py:104-105` and `:121-122`) are substring checks like `assert "an error message that should appear" in content` — the message survives as the JSON `event` value, so they pass either way. **They cannot tell plaintext from JSON**, which means nothing currently verifies the format actually changed.

Strengthen them. In `test_configure_logging_verbose_false_suppresses_info_but_logs_error`, replace lines 103-105:

```python
    lines = [line for line in log_path.read_text().splitlines() if line]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["level"] == "ERROR"
    assert payload["event"] == "an error message that should appear"
    assert payload["logger"] == "keel.somemodule"
```

In `test_configure_logging_verbose_true_logs_info_and_error`, replace lines 120-122:

```python
    lines = [line for line in log_path.read_text().splitlines() if line]
    assert len(lines) == 2
    payloads = [json.loads(line) for line in lines]
    assert [p["level"] for p in payloads] == ["INFO", "ERROR"]
    assert payloads[0]["event"] == "an info message that should appear"
    assert payloads[1]["event"] == "an error message that should appear too"
```

Add `import json` to the file's imports.

The remaining assertions (handler count, `maxBytes`, `backupCount`, logger levels, directory creation) test rotation and level config, which this task does not touch — leave them alone.

- [ ] **Step 6b: Verify the strengthened tests actually exercise the new format**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: all pass.

Then confirm they would catch a regression — temporarily revert `handler.setFormatter(JsonFormatter())` to `logging.Formatter("%(message)s")` and re-run:

Expected: FAIL with `json.decoder.JSONDecodeError`.

Restore `JsonFormatter()` and re-run to confirm green. A test that cannot fail is not protecting anything, and this is the one place in the task where that risk is real.

- [ ] **Step 7: Verify the full suite and baseline**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass; ruff clean.

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed — unchanged golden file.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: structured JSON telemetry with cycle correlation"
```

---

### Task 4: Migrate call sites to structured events

Converts the 36 existing `logger.*` calls in `agent.py`, `execution/guards.py`, `execution/executor.py`, `data/cb_client.py`, and `strategy/engine.py` from interpolated strings to `log_event` with stable names, and generates a `cycle_id` per engine cycle.

**Files:**
- Modify: `keel/agent.py`
- Modify: `keel/execution/guards.py`
- Modify: `keel/execution/executor.py`
- Modify: `keel/data/cb_client.py`
- Modify: `keel/strategy/engine.py`
- Create: `tests/test_cycle_correlation.py`

**Interfaces:**
- Consumes: `keel_core.telemetry.{log_event, bind_cycle, new_cycle_id, current_cycle}` from Task 3.
- Produces: every engine-cycle event carries a `cycle_id`. No new public API.

- [ ] **Step 1: Enumerate the call sites**

Run: `grep -rn "logger\.\(info\|error\|exception\|warning\|debug\)" keel --include="*.py" | grep -v __pycache__`
Expected: 36 lines across the five modules listed above.

Work through them file by file. Record the list — every line must be converted or explicitly left alone.

- [ ] **Step 2: Write the failing correlation test**

Create `tests/test_cycle_correlation.py`:

```python
"""Every event emitted inside one `agent.run_once` cycle shares a `cycle_id`."""

from __future__ import annotations

import json
import logging

from keel_core import telemetry


def test_cycle_id_is_stable_within_a_bound_cycle(caplog) -> None:
    formatter = telemetry.JsonFormatter()
    cycle = telemetry.new_cycle_id()
    telemetry.bind_cycle(cycle)
    try:
        with caplog.at_level(logging.INFO, logger="keel.test"):
            log = logging.getLogger("keel.test")
            telemetry.log_event(log, logging.INFO, "agent.cycle_start")
            telemetry.log_event(log, logging.INFO, "agent.cycle_end")
        ids = {json.loads(formatter.format(r))["cycle_id"] for r in caplog.records}
        assert ids == {cycle}
    finally:
        telemetry.bind_cycle(None)


def test_cycle_ids_differ_across_cycles() -> None:
    first = telemetry.new_cycle_id()
    second = telemetry.new_cycle_id()
    assert first != second
```

- [ ] **Step 3: Run it to verify it passes**

Run: `uv run pytest tests/test_cycle_correlation.py -v`
Expected: 2 passed. (This test guards Task 3's contract; the behavioural work is Step 4.)

- [ ] **Step 4: Bind a cycle id in `run_once`**

Modify `keel/agent.py`. Add the import:

```python
from keel_core.telemetry import bind_cycle, log_event, new_cycle_id
```

At the top of `run_once` (currently `agent.py:292`), before the existing `logger.info("agent.run_once: cycle start ts=%s", now_ts)`, bind a fresh id:

```python
bind_cycle(new_cycle_id())
```

Convert the cycle-start line:

```python
log_event(logger, logging.INFO, "agent.cycle_start", now_ts=now_ts)
```

Convert the kill-switch line at `agent.py:304`:

```python
log_event(logger, logging.INFO, "agent.cycle_skipped", reason="kill_switch")
```

Convert the mode line at `agent.py:328`:

```python
log_event(logger, logging.INFO, "agent.mode_resolved", mode=str(mode))
```

Add `import logging` if not already present.

- [ ] **Step 5: Convert the remaining call sites**

For each remaining line from Step 1, apply the same transformation:

- The interpolated message becomes a stable dotted `event` name derived from module and action — `guards.check_failed`, `executor.order_previewed`, `executor.order_placed`, `cb_client.request_failed`, `engine.setup_detected`.
- Every interpolated value becomes a keyword field: `logger.info("... product=%s", pid)` → `log_event(logger, logging.INFO, "engine.setup_detected", product=pid)`.
- `logger.exception(...)` becomes `log_event(logger, logging.ERROR, "<event>", ...)` inside the `except` block — `JsonFormatter` picks up `exc_info` only when the logger call sets it, so preserve it explicitly:

```python
logger.log(logging.ERROR, "executor.order_failed", exc_info=True, extra={"keel_fields": {"product": product_id}})
```

Prefer `log_event` everywhere it suffices; use the raw `logger.log(..., exc_info=True, extra=...)` form only where a traceback must be preserved.

- [ ] **Step 6: Verify no interpolated log messages remain**

Run: `grep -rn 'logger\.\(info\|error\|warning\|debug\|exception\)(' keel --include="*.py" | grep -v __pycache__ | grep '%s\|%d\|f"'`
Expected: no output.

- [ ] **Step 7: Verify the full suite, baseline, and lint**

Run: `uv run pytest -q`
Expected: all pass. Any test asserting on old plaintext log content must be updated to parse JSON and assert on `event` plus fields — not deleted.

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed — unchanged golden file.

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 8: Verify events end-to-end against the real log file**

`monitor` polls once by default (`--loop` is the opt-in for repeated polling), so a single
invocation produces one cycle's events.

Run: `uv run keel -v monitor; tail -3 logs/keel.log`
Expected: single-line JSON objects, each with `ts`, `level`, `logger`, `event`, and cycle-scoped
lines sharing one `cycle_id`.

This requires network access and valid Coinbase credentials. If unavailable, exercise the same
path offline instead:

```bash
uv run python -c "
import logging
from keel_core.config import LoggingConfig
from keel_core.logging_setup import configure_logging
from keel_core.telemetry import bind_cycle, log_event, new_cycle_id

configure_logging(LoggingConfig(verbose=True, file='/tmp/keel-verify.log'))
log = logging.getLogger('keel.verify')
bind_cycle(new_cycle_id())
log_event(log, logging.INFO, 'agent.cycle_start', now_ts=1)
log_event(log, logging.INFO, 'agent.cycle_end', now_ts=1)
for h in logging.getLogger('keel').handlers: h.flush()
"
cat /tmp/keel-verify.log

```

Expected: two JSON lines sharing one `cycle_id`, each with `event` set to `agent.cycle_start`
and `agent.cycle_end`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: migrate log call sites to structured events with cycle_id"
```

---

## Done criteria

- `uv run pytest -q` passes; `uv run ruff check .` clean.
- `tests/fixtures/baseline_backtest.json` is byte-identical to its Task 1 generation.
- `packages/keel-core` imports nothing from `keel`.
- No `logger.*` call in `keel/` uses `%s` or an f-string message.
- `halal_cb/` is gone.

## Follow-on

Spec §12 steps 3–5 (`keel-broker-api` + adapter extraction, `keel-data`/`keel-security` with the `venue` migration, `keel-strategy` with the `promotion.py` split) each warrant their own plan, written against the workspace this one creates. Step 3 is the natural next one: it retires the `broker: Any` and `order_configuration: dict` leaks and removes `coinbase-advanced-py` from the root dependency set.
