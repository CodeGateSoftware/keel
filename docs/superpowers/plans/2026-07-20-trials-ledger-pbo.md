# Trials Ledger + PBO/CSCV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an append-only, hash-chained trials ledger and a PBO/CSCV overfitting diagnostic, so that
every future parameter sweep can be scored honestly against the search that produced it.

**Architecture:** A new `keel/research/` package holds three modules — `ledger.py` (hash-chained JSONL
trial records), `cscv.py` (the CSCV/PBO algorithm plus its three companion statistics), and `matrix.py`
(builds the `(T × N)` per-bar P&L matrix from a declared candidate grid). `promotion.py` gains a G4 gate
that fires only on the conjunction of high PBO **and** a steeply negative degradation slope.
`report.py` and `cli.py` expose the results.

**Tech Stack:** Python 3.12, stdlib only (`itertools`, `hashlib`, `json`, `decimal`, `math`), `click` for
CLI, `pytest`, `ruff`. **No NumPy/Pandas/SciPy** — a standing project constraint (main spec §10).

**Spec:** `docs/superpowers/specs/2026-07-20-trials-ledger-pbo-design.md`

## Global Constraints

- **`Decimal` everywhere, never `float`.** Matches `keel/sim/metrics.py`. The only transcendental is
  `log`; use `Decimal.ln()`.
- **Stdlib only.** No new dependencies in `pyproject.toml`.
- **Line length 100**, ruff `select = ["E", "F", "I", "UP"]`. Run `uv run ruff check` before every commit.
- **`uv run pytest -q` must be green before every commit.** Baseline at plan time: 707+ tests.
- ⛔ **Strathern rail (spec §6):** PBO/DSR/haircut may **gate or report, never rank**. No function in
  `keel/research/` may return the identity of a best-performing configuration.
- **Config note (spec drift):** the spec says `keel/config.py`, but that is now a shim —
  the real dataclasses live in `packages/keel-core/keel_core/config.py`. Edit that file.
- Branch: `feat/trials-ledger-pbo`. Commit after every task.

---

## File Structure

| file | responsibility |
|---|---|
| `keel/research/__init__.py` | empty package marker |
| `keel/research/ledger.py` | `TrialRecord`, hash chain, `append_trial`/`read_trials`/`verify_chain`/`trial_counts` |
| `keel/research/cscv.py` | `pbo()`, `PBOResult`, block-aggregate Sortino, the three §78.8 companions |
| `keel/research/matrix.py` | `build_matrix()` — candidate grid → `(T × N)` columns, refusing `series_missing` rows |
| `keel/strategy/promotion.py` | **modify** — `PBOGate`, `g4_pbo_gate()` |
| `keel/sim/report.py` | **modify** — `_render_pbo_section()` |
| `keel/cli.py` | **modify** — `keel trials {record,list,verify,pbo}`; auto-record in `simulate` |
| `packages/keel-core/keel_core/config.py` | **modify** — `ResearchConfig` |
| `config.yaml` | **modify** — `research:` block |
| `docs/experiments/trials-ledger.jsonl` | the ledger itself (git-tracked) |
| `tests/research/test_ledger.py` | ledger + hash chain tests |
| `tests/research/test_cscv.py` | CSCV algorithm + power-replication tests |
| `tests/research/test_matrix.py` | matrix builder tests |
| `tests/strategy/test_promotion.py` | **modify** — G4 truth table |

---

### Task 1: The ledger and its hash chain

**Files:**
- Create: `keel/research/__init__.py`, `keel/research/ledger.py`
- Create: `tests/research/__init__.py`, `tests/research/test_ledger.py`

**Interfaces:**
- Produces: `TrialRecord` (frozen dataclass), `append_trial(path, **fields) -> TrialRecord`,
  `read_trials(path) -> list[TrialRecord]`, `verify_chain(path) -> list[str]`,
  `trial_counts(trials) -> tuple[int, int]`, constants `PROVENANCE`, `KINDS`, `DECISIONS`, `ZERO_HASH`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/test_ledger.py
"""Ledger + hash-chain tests (spec §4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research import ledger


def _append(path, trial_id: str, **over):
    kwargs = dict(
        trial_id=trial_id,
        session="test-session",
        rule="turtle_breakout",
        params={"entry": 40, "exit": 20},
        provenance="fitted",
        kind="sweep_node",
        decision="selected",
        per_trade_pnl=[Decimal("1.5"), Decimal("-2")],
        per_bar_pnl=[Decimal("0.1")],
        summary={"sr_trade": Decimal("0.3"), "expectancy": Decimal("610"), "trade_count": 31},
        timestamp=1_700_000_000,
    )
    kwargs.update(over)
    return ledger.append_trial(path, **kwargs)


def test_first_row_chains_to_zero_hash(tmp_path):
    path = tmp_path / "trials.jsonl"
    row = _append(path, "t1")
    assert row.prev_hash == ledger.ZERO_HASH
    assert len(row.row_hash) == 64


def test_second_row_chains_to_first(tmp_path):
    path = tmp_path / "trials.jsonl"
    first = _append(path, "t1")
    second = _append(path, "t2")
    assert second.prev_hash == first.row_hash


def test_roundtrip_preserves_decimal_exactly(tmp_path):
    path = tmp_path / "trials.jsonl"
    _append(path, "t1", per_trade_pnl=[Decimal("0.1"), Decimal("0.2")])
    (row,) = ledger.read_trials(path)
    assert row.per_trade_pnl == [Decimal("0.1"), Decimal("0.2")]
    assert row.summary["expectancy"] == Decimal("610")


def test_verify_chain_passes_on_untampered_file(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    assert ledger.verify_chain(path) == []


def test_tamper_is_detected_at_the_edited_row_and_after(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"expectancy":"610"', '"expectancy":"99999"')
    path.write_text("\n".join(lines) + "\n")

    errors = ledger.verify_chain(path)
    assert len(errors) == 2
    assert "row 2" in errors[0]
    assert "row 3" in errors[1]


def test_deletion_is_detected(tmp_path):
    path = tmp_path / "trials.jsonl"
    for i in range(3):
        _append(path, f"t{i}")
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n")
    assert ledger.verify_chain(path) != []


def test_rejects_unknown_enum_values(tmp_path):
    path = tmp_path / "trials.jsonl"
    with pytest.raises(ValueError, match="provenance"):
        _append(path, "t1", provenance="vibes")
    with pytest.raises(ValueError, match="kind"):
        _append(path, "t1", kind="hunch")
    with pytest.raises(ValueError, match="decision"):
        _append(path, "t1", decision="maybe")


def test_series_missing_row_may_omit_series(tmp_path):
    path = tmp_path / "trials.jsonl"
    row = _append(path, "t1", per_trade_pnl=[], per_bar_pnl=[], series_missing=True)
    assert row.series_missing is True
    assert row.per_bar_pnl == []


def test_series_missing_false_requires_a_series(tmp_path):
    path = tmp_path / "trials.jsonl"
    with pytest.raises(ValueError, match="series_missing"):
        _append(path, "t1", per_trade_pnl=[], per_bar_pnl=[], series_missing=False)


def test_trial_counts_splits_m_from_decision_count(tmp_path):
    path = tmp_path / "trials.jsonl"
    _append(path, "t1", decision="selected")
    _append(path, "t2", decision="rejected")
    _append(path, "t3", decision="diagnostic_only")
    m, n_decisions = ledger.trial_counts(ledger.read_trials(path))
    assert m == 3
    assert n_decisions == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/research/test_ledger.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'keel.research'`

- [ ] **Step 3: Implement `keel/research/ledger.py`**

Create `keel/research/__init__.py` (empty) and `tests/research/__init__.py` (empty), then:

```python
"""Append-only, hash-chained trials ledger (spec §4).

Records *experiments*, never money: a row is "swept `donchian_entry_n` over {20,40,55},
selected 40, provenance `fitted`". The live trade record lives in `keel.db` and must never
come here -- see spec §2.1 for why git-tracking is safe only under that boundary.

Storage is JSONL so it is append-only by nature, diffable in review, and needs no migration
when the schema grows. `Decimal` is serialised as a string, matching the repo's TEXT-money
convention. Each row carries `prev_hash` (the previous row's `row_hash`) and its own
`row_hash`, so editing or deleting any row breaks verification for that row and every row
after it -- tamper-EVIDENT, not tamper-proof (spec §4.3).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

PROVENANCE = frozenset({"a_priori", "fitted"})
KINDS = frozenset(
    {"sweep_node", "ablation", "rule_retirement", "asset_prune", "threshold_nudge"}
)
DECISIONS = frozenset({"selected", "rejected", "diagnostic_only"})

#: A CSCV column is a diagnostic, not a decision (spec §4.4) -- it does not count toward N.
DIAGNOSTIC_ONLY = "diagnostic_only"

ZERO_HASH = "0" * 64

DEFAULT_LEDGER_PATH = Path("docs/experiments/trials-ledger.jsonl")


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    timestamp: int
    session: str
    rule: str
    params: dict[str, Any]
    provenance: str
    kind: str
    decision: str
    per_trade_pnl: list[Decimal] = field(default_factory=list)
    per_bar_pnl: list[Decimal] = field(default_factory=list)
    series_missing: bool = False
    summary: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = ZERO_HASH
    row_hash: str = ""


def _encode(value: Any) -> Any:
    """Decimal -> str so JSON round-trips exactly; recurse through containers."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode_series(raw: Any) -> list[Decimal]:
    return [Decimal(v) for v in (raw or [])]


def _decode_summary(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        # trade_count is a plain int; every other summary field is money/ratio -> Decimal.
        out[key] = value if isinstance(value, int) else Decimal(value)
    return out


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic serialisation: sorted keys, no incidental whitespace.

    The hash is only reproducible if this is byte-stable, so both the separators and the
    key ordering are pinned here rather than left to `json.dumps` defaults.
    """
    return json.dumps(_encode(dict(payload)), sort_keys=True, separators=(",", ":"))


def _row_payload(record: TrialRecord) -> dict[str, Any]:
    """Everything that is hashed -- i.e. the row minus `row_hash` itself."""
    return {
        "trial_id": record.trial_id,
        "timestamp": record.timestamp,
        "session": record.session,
        "rule": record.rule,
        "params": record.params,
        "provenance": record.provenance,
        "kind": record.kind,
        "decision": record.decision,
        "per_trade_pnl": record.per_trade_pnl,
        "per_bar_pnl": record.per_bar_pnl,
        "series_missing": record.series_missing,
        "summary": record.summary,
        "prev_hash": record.prev_hash,
    }


def compute_row_hash(record: TrialRecord) -> str:
    return hashlib.sha256(canonical_json(_row_payload(record)).encode("utf-8")).hexdigest()


def _validate(record: TrialRecord) -> None:
    if record.provenance not in PROVENANCE:
        raise ValueError(f"provenance: {record.provenance!r} not in {sorted(PROVENANCE)}")
    if record.kind not in KINDS:
        raise ValueError(f"kind: {record.kind!r} not in {sorted(KINDS)}")
    if record.decision not in DECISIONS:
        raise ValueError(f"decision: {record.decision!r} not in {sorted(DECISIONS)}")
    if not record.series_missing and not (record.per_trade_pnl or record.per_bar_pnl):
        raise ValueError(
            "series_missing is False but no P&L series was supplied; a trial with no series "
            "must say so explicitly (spec §4.6) so the CSCV matrix can refuse it"
        )


def _last_hash(path: Path) -> str:
    if not path.exists():
        return ZERO_HASH
    last = ZERO_HASH
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = json.loads(line)["row_hash"]
    return last


def append_trial(
    path: Path | str,
    *,
    trial_id: str,
    session: str,
    rule: str,
    params: Mapping[str, Any],
    provenance: str,
    kind: str,
    decision: str,
    per_trade_pnl: Sequence[Decimal] = (),
    per_bar_pnl: Sequence[Decimal] = (),
    series_missing: bool = False,
    summary: Mapping[str, Any] | None = None,
    timestamp: int | None = None,
) -> TrialRecord:
    """Validate, chain and append one trial. Returns the stored record."""
    path = Path(path)
    record = TrialRecord(
        trial_id=trial_id,
        timestamp=int(time.time()) if timestamp is None else timestamp,
        session=session,
        rule=rule,
        params=dict(params),
        provenance=provenance,
        kind=kind,
        decision=decision,
        per_trade_pnl=list(per_trade_pnl),
        per_bar_pnl=list(per_bar_pnl),
        series_missing=series_missing,
        summary=dict(summary or {}),
        prev_hash=_last_hash(path),
    )
    _validate(record)
    record = TrialRecord(**{**record.__dict__, "row_hash": compute_row_hash(record)})

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _row_payload(record)
    payload["row_hash"] = record.row_hash
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")
    return record


def read_trials(path: Path | str) -> list[TrialRecord]:
    path = Path(path)
    if not path.exists():
        return []
    trials: list[TrialRecord] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            trials.append(
                TrialRecord(
                    trial_id=raw["trial_id"],
                    timestamp=raw["timestamp"],
                    session=raw["session"],
                    rule=raw["rule"],
                    params=raw["params"],
                    provenance=raw["provenance"],
                    kind=raw["kind"],
                    decision=raw["decision"],
                    per_trade_pnl=_decode_series(raw.get("per_trade_pnl")),
                    per_bar_pnl=_decode_series(raw.get("per_bar_pnl")),
                    series_missing=raw.get("series_missing", False),
                    summary=_decode_summary(raw.get("summary")),
                    prev_hash=raw["prev_hash"],
                    row_hash=raw["row_hash"],
                )
            )
    return trials


def verify_chain(path: Path | str) -> list[str]:
    """Return a list of human-readable chain errors; empty means intact.

    Reports rather than raises: a broken chain is a finding to surface in a report, and the
    caller usually wants every break rather than only the first.
    """
    errors: list[str] = []
    expected_prev = ZERO_HASH
    for index, record in enumerate(read_trials(path), start=1):
        if record.prev_hash != expected_prev:
            errors.append(
                f"row {index} ({record.trial_id}): prev_hash {record.prev_hash[:12]}... "
                f"does not chain to {expected_prev[:12]}..."
            )
        elif compute_row_hash(record) != record.row_hash:
            errors.append(f"row {index} ({record.trial_id}): content does not match row_hash")
        expected_prev = record.row_hash
    return errors


def trial_counts(trials: Iterable[TrialRecord]) -> tuple[int, int]:
    """`(M, N_decisions)` -- spec §4.4. Diagnostics count toward M but not toward N."""
    trials = list(trials)
    return len(trials), sum(1 for t in trials if t.decision != DIAGNOSTIC_ONLY)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/research/test_ledger.py -q`
Expected: 10 passed

Note the tamper test asserts the break is reported at row 2 **and** row 3 — editing row 2 changes its
`row_hash`, so row 3's `prev_hash` no longer chains. That propagation is the property being bought.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/research tests/research
git commit -m "feat(research): hash-chained append-only trials ledger

Records experiments, never money (spec §2.1). Each row chains to the
previous via sha256, so an edit or deletion breaks verification for that
row and every row after it. trial_counts() keeps M and N_decisions
separate: a CSCV column is a diagnostic, not a decision (spec §4.4)."
```

---

### Task 2: `keel trials record|list|verify`

**Files:**
- Modify: `keel/cli.py` (add a `trials` group near the existing `db` group at line ~240)
- Create: `tests/research/test_trials_cli.py`

**Interfaces:**
- Consumes: `ledger.append_trial`, `ledger.read_trials`, `ledger.verify_chain`,
  `ledger.trial_counts`, `ledger.DEFAULT_LEDGER_PATH` from Task 1.
- Produces: CLI commands `keel trials record`, `keel trials list`, `keel trials verify`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/test_trials_cli.py
"""CLI surface for the trials ledger (spec §4.5) -- the scratchpad recording path."""

from __future__ import annotations

from click.testing import CliRunner

from keel.cli import cli


def _record(runner, path, trial_id, decision="selected"):
    return runner.invoke(
        cli,
        [
            "trials", "record",
            "--ledger", str(path),
            "--trial-id", trial_id,
            "--session", "s1",
            "--rule", "turtle_breakout",
            "--params", '{"entry": 40}',
            "--provenance", "fitted",
            "--kind", "sweep_node",
            "--decision", decision,
            "--series-missing",
        ],
    )


def test_record_then_list_and_verify(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"

    assert _record(runner, path, "t1").exit_code == 0
    assert _record(runner, path, "t2", decision="diagnostic_only").exit_code == 0

    listed = runner.invoke(cli, ["trials", "list", "--ledger", str(path)])
    assert listed.exit_code == 0
    assert "t1" in listed.output
    assert "M=2" in listed.output
    assert "N_decisions=1" in listed.output

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code == 0
    assert "intact" in verified.output.lower()


def test_verify_exits_nonzero_on_tamper(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    _record(runner, path, "t1")
    _record(runner, path, "t2")

    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace('"s1"', '"s2"')
    path.write_text("\n".join(lines) + "\n")

    verified = runner.invoke(cli, ["trials", "verify", "--ledger", str(path)])
    assert verified.exit_code != 0
    assert "row 1" in verified.output


def test_record_rejects_bad_enum(tmp_path):
    runner = CliRunner()
    path = tmp_path / "trials.jsonl"
    result = runner.invoke(
        cli,
        [
            "trials", "record", "--ledger", str(path), "--trial-id", "t1",
            "--session", "s", "--rule", "r", "--params", "{}",
            "--provenance", "vibes", "--kind", "sweep_node",
            "--decision", "selected", "--series-missing",
        ],
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_trials_cli.py -q`
Expected: FAIL — `No such command 'trials'`

- [ ] **Step 3: Add the `trials` group to `keel/cli.py`**

Add near the top with the other imports:

```python
from keel.research import ledger as trials_ledger
```

Then append this group (placement: after the `db` group, before `monitor`):

```python
@cli.group("trials")
def trials_group() -> None:
    """Append-only trials ledger (spec §4). Records experiments, never money."""


_LEDGER_OPTION = click.option(
    "--ledger",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Ledger path (default: docs/experiments/trials-ledger.jsonl).",
)


def _ledger_path(ledger: Path | None) -> Path:
    return ledger if ledger is not None else trials_ledger.DEFAULT_LEDGER_PATH


@trials_group.command("record")
@_LEDGER_OPTION
@click.option("--trial-id", required=True)
@click.option("--session", required=True, help="Free-text experiment/session label.")
@click.option("--rule", required=True)
@click.option("--params", required=True, help="JSON object of the full parameter dict.")
@click.option("--provenance", required=True, type=click.Choice(sorted(trials_ledger.PROVENANCE)))
@click.option("--kind", required=True, type=click.Choice(sorted(trials_ledger.KINDS)))
@click.option("--decision", required=True, type=click.Choice(sorted(trials_ledger.DECISIONS)))
@click.option("--series-missing", is_flag=True, default=False)
@click.option("--per-bar-pnl", default=None, help="JSON array of per-bar P&L.")
@click.option("--per-trade-pnl", default=None, help="JSON array of per-trade P&L.")
def trials_record(
    ledger: Path | None,
    trial_id: str,
    session: str,
    rule: str,
    params: str,
    provenance: str,
    kind: str,
    decision: str,
    series_missing: bool,
    per_bar_pnl: str | None,
    per_trade_pnl: str | None,
) -> None:
    """Record one trial -- the path scratchpad experiments use (spec §4.5)."""

    def _series(raw: str | None) -> list[Decimal]:
        return [Decimal(str(v)) for v in json.loads(raw)] if raw else []

    try:
        record = trials_ledger.append_trial(
            _ledger_path(ledger),
            trial_id=trial_id,
            session=session,
            rule=rule,
            params=json.loads(params),
            provenance=provenance,
            kind=kind,
            decision=decision,
            per_trade_pnl=_series(per_trade_pnl),
            per_bar_pnl=_series(per_bar_pnl),
            series_missing=series_missing,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"recorded {record.trial_id} ({record.decision}) hash={record.row_hash[:12]}")


@trials_group.command("list")
@_LEDGER_OPTION
def trials_list(ledger: Path | None) -> None:
    """List recorded trials and the two N accountings (spec §4.4)."""
    trials = trials_ledger.read_trials(_ledger_path(ledger))
    for index, record in enumerate(trials, start=1):
        flag = " [series_missing]" if record.series_missing else ""
        click.echo(
            f"{index:>4}  {record.trial_id:<28} {record.rule:<20} "
            f"{record.provenance:<9} {record.kind:<17} {record.decision}{flag}"
        )
    m, n_decisions = trials_ledger.trial_counts(trials)
    click.echo(f"\nM={m}  N_decisions={n_decisions}")


@trials_group.command("verify")
@_LEDGER_OPTION
def trials_verify(ledger: Path | None) -> None:
    """Verify the hash chain. Exits non-zero if broken."""
    errors = trials_ledger.verify_chain(_ledger_path(ledger))
    if not errors:
        click.echo("chain intact")
        return
    for error in errors:
        click.echo(error, err=True)
    raise click.ClickException(f"{len(errors)} chain error(s)")
```

If `json`, `Decimal` or `Path` are not already imported in `cli.py`, add them.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/research/test_trials_cli.py -q`
Expected: 3 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/cli.py tests/research/test_trials_cli.py
git commit -m "feat(cli): keel trials record|list|verify

The scratchpad recording path (spec §4.5). Auto-recording from simulate
alone would have missed nearly every trial in this project's history --
the walk-forward, the ADX ablation and rank-markets all ran as scratchpad
scripts by design."
```

---

### Task 3: CSCV core — `pbo()`

**Files:**
- Create: `keel/research/cscv.py`
- Create: `tests/research/test_cscv.py`

**Interfaces:**
- Produces: `PBOResult` (frozen dataclass), `pbo(columns, s=16, metric=None) -> PBOResult`,
  `sortino_from_aggregates(n, total, downside_sq) -> Decimal`, `block_aggregates(column, s)`.
- `columns` is **N columns each of length T** (not T rows). `pbo` truncates the **oldest** rows so
  `T` divides `S`.

**Key design notes for the implementer:**

1. **Ranks are 1..N with N = best**, matching the paper's `r_{n*} = N`. Ties break by column index so
   the result stays deterministic (§78.7: *"running CSCV twice on the same inputs generates identical
   results"*).
2. **Sortino is decomposable**: `count`, `Σr`, `Σ(r⁻)²` are additive across blocks, so precompute them
   once per (block × column) and each combination is `O(S/2)` instead of `O(T)`. Test-set aggregates are
   obtained by **subtracting** the training aggregate from the column total — the complement, for free.
3. Downside variance divides by the **full** `n` and targets 0, matching
   `keel/sim/metrics._downside_variance`. Do not invent a different convention.
4. Sortino here is **not annualised**. Ranking is invariant to a positive scale factor, and the
   degradation slope scales identically on both axes, so annualising would only add noise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/test_cscv.py
"""CSCV / PBO tests (spec §5). The power replication is the load-bearing one."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from keel.research import cscv


def _const_columns(values: list[float], t: int = 32) -> list[list[Decimal]]:
    """N columns, column n being a constant-drift series with a fixed alternating wiggle.

    The wiggle guarantees a non-zero downside deviation so Sortino is well defined.
    """
    columns = []
    for value in values:
        column = []
        for i in range(t):
            column.append(Decimal(str(value)) + (Decimal("1") if i % 2 else Decimal("-1")))
        columns.append(column)
    return columns


def test_combination_count_is_c_16_8():
    assert cscv.combination_count(16) == 12870


def test_rejects_odd_s():
    with pytest.raises(ValueError, match="even"):
        cscv.pbo(_const_columns([1.0, 2.0]), s=7)


def test_perfectly_consistent_columns_give_pbo_zero():
    # Column ordering is identical in every subsample, so the IS-best is always OOS-best.
    result = cscv.pbo(_const_columns([1.0, 2.0, 3.0, 4.0, 5.0]), s=4)
    assert result.pbo == Decimal(0)


def test_pbo_is_deterministic():
    columns = _const_columns([1.0, 2.0, 3.0, 4.0, 5.0])
    assert cscv.pbo(columns, s=4).pbo == cscv.pbo(columns, s=4).pbo


def test_truncation_drops_oldest_rows():
    # 10 rows, s=4 -> 8 kept, the 2 OLDEST dropped.
    columns = [[Decimal(i) for i in range(10)], [Decimal(-i) for i in range(10)]]
    kept = cscv.truncate_to_blocks(columns, s=4)
    assert len(kept[0]) == 8
    assert kept[0][0] == Decimal(2)


def test_block_aggregate_sortino_matches_direct_computation():
    column = [Decimal(str(v)) for v in (1, -2, 3, -4, 5, -6, 7, -8)]
    aggregates = cscv.block_aggregates(column, s=4)
    total_n = sum(a[0] for a in aggregates)
    total_sum = sum((a[1] for a in aggregates), Decimal(0))
    total_dsq = sum((a[2] for a in aggregates), Decimal(0))

    fast = cscv.sortino_from_aggregates(total_n, total_sum, total_dsq)
    slow = cscv.sortino_series(column)
    assert fast == slow


def _random_walk_columns(n: int, t: int, seed: int) -> list[list[Decimal]]:
    rng = random.Random(seed)
    return [
        [Decimal(str(round(rng.gauss(0, 1), 4))) for _ in range(t)]
        for _ in range(n)
    ]


def test_power_replication_noise_versus_injected_signal():
    """§78.8's calibration: CSCV must have POWER, not merely conservatism.

    Pure noise should land near the paper's 0.55; the same matrix with a genuine signal
    injected into one column should drop sharply. An implementation that cannot separate
    these is wrong regardless of what it reports on the Turtle.
    """
    noise = _random_walk_columns(n=12, t=256, seed=1234)
    noise_pbo = cscv.pbo(noise, s=8).pbo

    signal = [list(column) for column in noise]
    # Give column 0 a persistent positive drift present in EVERY subsample.
    signal[0] = [value + Decimal("0.9") for value in signal[0]]
    signal_pbo = cscv.pbo(signal, s=8).pbo

    assert noise_pbo > Decimal("0.25")
    assert signal_pbo < Decimal("0.10")
    assert signal_pbo < noise_pbo


def test_result_exposes_no_configuration_field():
    """⛔ Strathern rail (spec §6): PBO may gate or report, never rank.

    If a caller could read the winning configuration out of a diagnostic run, CSCV becomes
    a selection tool -- the exact misuse §78.7 warns against. Guard it structurally.
    """
    banned = {"best_config", "best_column", "best_index", "argmax", "selected", "params",
              "winner", "best_n", "n_star"}
    fields = set(cscv.PBOResult.__dataclass_fields__)
    assert not (fields & banned)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_cscv.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel.research.cscv'`

- [ ] **Step 3: Implement `keel/research/cscv.py`**

```python
"""Probability of Backtest Overfitting via CSCV (spec §5, KB §78.6).

Model-free, non-parametric and deterministic: no distributional assumption, no forecasting
model, no knowledge of the trading rule -- only the matrix of per-period P&L across the
configurations tried.

⛔ STRATHERN RAIL (spec §6). Nothing here returns the identity of a best-performing
configuration. PBO may gate or report; it may never be a sweep's ranking key, because
"when a measure becomes a target, it ceases to be a good measure" (§78.7). `PBOResult`
therefore carries probabilities and slopes only -- a test in `tests/research/test_cscv.py`
fails if a configuration-bearing field is ever added.

PERFORMANCE. The naive form is ~140M Decimal operations (12,870 combinations x ~12 columns
x 904 rows) -- tens of minutes. Sortino is *decomposable*: `count`, `sum(r)` and
`sum(r^2 for r < 0)` are additive across blocks, so we precompute those three aggregates
once per (block x column) and each combination becomes O(S/2). ~1.2M operations, seconds,
in exact Decimal. Drawdown-based metrics are NOT decomposable and take the slow path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from math import comb

#: Sentinels for a subsample with zero downside. `+/-INFINITY_RANK` only ever participates
#: in ordering, never in arithmetic, so a finite stand-in is safe and keeps everything Decimal.
_POSITIVE_SENTINEL = Decimal("1e18")
_NEGATIVE_SENTINEL = Decimal("-1e18")

Aggregate = tuple[int, Decimal, Decimal]  # (count, sum, sum of squared negatives)
Metric = Callable[[Sequence[Decimal]], Decimal]


@dataclass(frozen=True)
class PBOResult:
    """Probabilities and slopes only -- never a configuration (see the Strathern rail above)."""

    pbo: Decimal
    n_combinations: int
    n_columns: int
    n_blocks: int
    rows_used: int
    rows_dropped: int
    logits: list[Decimal]
    is_performance: list[Decimal]
    oos_performance: list[Decimal]
    degradation_slope: Decimal = Decimal(0)
    degradation_intercept: Decimal = Decimal(0)
    prob_loss: Decimal = Decimal(0)
    dominance_1st: bool = False
    dominance_2nd: bool = False


def combination_count(s: int) -> int:
    return comb(s, s // 2)


def sortino_from_aggregates(count: int, total: Decimal, downside_sq: Decimal) -> Decimal:
    """Sortino from additive aggregates, target 0, rf = 0.

    Downside variance divides by the FULL count (not the negative-only subset), matching
    `keel/sim/metrics._downside_variance`. Not annualised: ranking is invariant to a
    positive scale factor and the degradation slope scales identically on both axes.
    """
    if count == 0:
        return Decimal(0)
    mean = total / count
    downside_var = downside_sq / count
    if downside_var <= 0:
        # No losing period at all: best possible if we made money, neutral if we did not.
        if mean > 0:
            return _POSITIVE_SENTINEL
        return Decimal(0) if mean == 0 else _NEGATIVE_SENTINEL
    return mean / downside_var.sqrt()


def sortino_series(returns: Sequence[Decimal]) -> Decimal:
    """Direct (slow-path) Sortino over a series -- the reference the fast path must match."""
    count = len(returns)
    total = sum(returns, Decimal(0))
    downside_sq = sum((r * r for r in returns if r < 0), Decimal(0))
    return sortino_from_aggregates(count, total, downside_sq)


def truncate_to_blocks(
    columns: Sequence[Sequence[Decimal]], s: int
) -> list[list[Decimal]]:
    """Trim to a multiple of `s`, dropping the OLDEST rows so the recent window stays intact."""
    length = min(len(column) for column in columns)
    usable = (length // s) * s
    return [list(column[length - usable :]) for column in columns]


def block_aggregates(column: Sequence[Decimal], s: int) -> list[Aggregate]:
    """`(count, sum, sum of squared negatives)` per block, blocks in original order."""
    size = len(column) // s
    out: list[Aggregate] = []
    for index in range(s):
        chunk = column[index * size : (index + 1) * size]
        out.append(
            (
                len(chunk),
                sum(chunk, Decimal(0)),
                sum((r * r for r in chunk if r < 0), Decimal(0)),
            )
        )
    return out


def _ranks(values: Sequence[Decimal]) -> list[int]:
    """Rank 1 = worst, len = best. Ties break by column index, keeping CSCV deterministic."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0] * len(values)
    for position, index in enumerate(order, start=1):
        ranks[index] = position
    return ranks


def _ols_slope(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    n = len(xs)
    if n < 2:
        return Decimal(0), Decimal(0)
    mean_x = sum(xs, Decimal(0)) / n
    mean_y = sum(ys, Decimal(0)) / n
    denominator = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
    if denominator == 0:
        return Decimal(0), mean_y
    numerator = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), Decimal(0))
    slope = numerator / denominator
    return slope, mean_y - slope * mean_x


def _empirical_cdf(sample: Sequence[Decimal], x: Decimal) -> Decimal:
    return Decimal(sum(1 for value in sample if value <= x)) / len(sample)


def _dominance(selected: Sequence[Decimal], reference: Sequence[Decimal]) -> tuple[bool, bool]:
    """First- and second-order stochastic dominance of `selected` over `reference` (§78.8).

    The direct test of "is our parameter selection better than picking a configuration at
    random?" -- §58.11's random-entry null lifted from entries to the selection process.
    """
    if not selected or not reference:
        return False, False
    grid = sorted(set(selected) | set(reference))
    first = True
    second = True
    running = Decimal(0)
    for x in grid:
        cdf_selected = _empirical_cdf(selected, x)
        cdf_reference = _empirical_cdf(reference, x)
        if cdf_selected > cdf_reference:
            first = False
        running += cdf_reference - cdf_selected
        if running < 0:
            second = False
    return first, second


def pbo(
    columns: Sequence[Sequence[Decimal]],
    s: int = 16,
    metric: Metric | None = None,
) -> PBOResult:
    """CSCV per Algorithm 2.3 (§78.6). `columns` is N columns of per-bar P&L, length T each.

    `metric=None` uses the fast decomposable Sortino path. Pass a callable (e.g. a
    return/max-drawdown ratio) to take the slow path; submatrices are joined in ORIGINAL
    ORDER either way, because order-dependent metrics require it and getting that wrong is
    silent.
    """
    if s % 2 != 0:
        raise ValueError(f"s must be even (got {s})")
    if not columns:
        raise ValueError("columns must not be empty")

    original_length = min(len(column) for column in columns)
    trimmed = truncate_to_blocks(columns, s)
    rows_used = len(trimmed[0])
    if rows_used == 0:
        raise ValueError(f"not enough rows ({original_length}) for s={s}")

    n_columns = len(trimmed)
    block_size = rows_used // s
    half = s // 2

    aggregates = [block_aggregates(column, s) for column in trimmed]
    totals: list[Aggregate] = [
        (
            sum(a[0] for a in column_aggregates),
            sum((a[1] for a in column_aggregates), Decimal(0)),
            sum((a[2] for a in column_aggregates), Decimal(0)),
        )
        for column_aggregates in aggregates
    ]

    logits: list[Decimal] = []
    is_selected: list[Decimal] = []
    oos_selected: list[Decimal] = []
    oos_means: list[Decimal] = []

    for training_blocks in combinations(range(s), half):
        training = set(training_blocks)
        testing_blocks = [i for i in range(s) if i not in training]

        if metric is None:
            is_performance = []
            oos_performance = []
            for column_index in range(n_columns):
                column_aggregates = aggregates[column_index]
                count = 0
                total = Decimal(0)
                downside = Decimal(0)
                for block_index in training_blocks:
                    block = column_aggregates[block_index]
                    count += block[0]
                    total += block[1]
                    downside += block[2]
                whole = totals[column_index]
                is_performance.append(sortino_from_aggregates(count, total, downside))
                # The complement, for free -- no second pass over the blocks.
                oos_performance.append(
                    sortino_from_aggregates(
                        whole[0] - count, whole[1] - total, whole[2] - downside
                    )
                )
        else:
            # Slow path: rebuild the actual series, joined in ORIGINAL block order.
            def _join(block_indices: Sequence[int], column: Sequence[Decimal]) -> list[Decimal]:
                joined: list[Decimal] = []
                for block_index in sorted(block_indices):
                    joined.extend(column[block_index * block_size : (block_index + 1) * block_size])
                return joined

            is_performance = [metric(_join(training_blocks, c)) for c in trimmed]
            oos_performance = [metric(_join(testing_blocks, c)) for c in trimmed]

        is_ranks = _ranks(is_performance)
        oos_ranks = _ranks(oos_performance)
        best = is_ranks.index(n_columns)  # the IS-best column; never returned to the caller

        omega = Decimal(oos_ranks[best]) / Decimal(n_columns + 1)
        logits.append((omega / (Decimal(1) - omega)).ln())
        is_selected.append(is_performance[best])
        oos_selected.append(oos_performance[best])
        oos_means.append(sum(oos_performance, Decimal(0)) / n_columns)

    n_combinations = len(logits)
    phi = Decimal(sum(1 for value in logits if value <= 0)) / Decimal(n_combinations)
    slope, intercept = _ols_slope(is_selected, oos_selected)
    prob_loss = Decimal(sum(1 for value in oos_selected if value < 0)) / Decimal(n_combinations)
    first, second = _dominance(oos_selected, oos_means)

    return PBOResult(
        pbo=phi,
        n_combinations=n_combinations,
        n_columns=n_columns,
        n_blocks=s,
        rows_used=rows_used,
        rows_dropped=original_length - rows_used,
        logits=logits,
        is_performance=is_selected,
        oos_performance=oos_selected,
        degradation_slope=slope,
        degradation_intercept=intercept,
        prob_loss=prob_loss,
        dominance_1st=first,
        dominance_2nd=second,
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/research/test_cscv.py -q`
Expected: 9 passed

If the power-replication test fails, **do not weaken its thresholds** — that test is the reason to trust
every other number this module produces. Debug the rank direction (`rank N = best`) and the
training/testing complement first; those are the two places this algorithm is usually wrong.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/research/cscv.py tests/research/test_cscv.py
git commit -m "feat(research): PBO via CSCV, with the Strathern rail enforced in code

Algorithm 2.3 (KB §78.6): partition into S blocks, all C(S,S/2) splits,
rank IS and OOS, PBO = fraction of combinations whose IS-best column
lands at or below the OOS median.

Sortino is decomposable across blocks (count, sum, sum of squared
negatives are additive), turning O(T) per combination into O(S/2) --
~1.2M Decimal ops instead of ~140M. OOS aggregates come free by
subtracting the training aggregate from the column total.

PBOResult carries no configuration-bearing field, and a test fails if one
is added: a caller must not be able to read a parameter choice out of a
diagnostic run (§78.7)."
```

---

### Task 4: The candidate-grid matrix builder

**Files:**
- Create: `keel/research/matrix.py`
- Create: `tests/research/test_matrix.py`

**Interfaces:**
- Consumes: `ledger.TrialRecord`, `ledger.read_trials` (Task 1).
- Produces: `build_matrix(trials, session=None) -> MatrixBuild` where
  `MatrixBuild = (columns: list[list[Decimal]], trial_ids: list[str], refused: list[str])`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/research/test_matrix.py
"""Matrix assembly for CSCV (spec §5.4)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.research import ledger, matrix


def _trial(trial_id, bars, series_missing=False, session="s1"):
    return ledger.TrialRecord(
        trial_id=trial_id,
        timestamp=0,
        session=session,
        rule="turtle_breakout",
        params={},
        provenance="fitted",
        kind="sweep_node",
        decision="diagnostic_only",
        per_trade_pnl=[],
        per_bar_pnl=[Decimal(str(b)) for b in bars],
        series_missing=series_missing,
        summary={},
    )


def test_builds_columns_from_trials():
    build = matrix.build_matrix([_trial("a", [1, 2, 3]), _trial("b", [3, 2, 1])])
    assert build.columns == [
        [Decimal(1), Decimal(2), Decimal(3)],
        [Decimal(3), Decimal(2), Decimal(1)],
    ]
    assert build.trial_ids == ["a", "b"]
    assert build.refused == []


def test_refuses_series_missing_rows():
    build = matrix.build_matrix(
        [_trial("a", [1, 2, 3]), _trial("backfilled", [], series_missing=True)]
    )
    assert build.trial_ids == ["a"]
    assert build.refused == ["backfilled"]


def test_requires_synchronous_rows():
    with pytest.raises(ValueError, match="synchronous"):
        matrix.build_matrix([_trial("a", [1, 2, 3]), _trial("b", [1, 2])])


def test_filters_by_session():
    build = matrix.build_matrix(
        [_trial("a", [1, 2], session="x"), _trial("b", [3, 4], session="y")], session="y"
    )
    assert build.trial_ids == ["b"]


def test_warns_below_ten_columns():
    build = matrix.build_matrix([_trial("a", [1, 2]), _trial("b", [2, 1])])
    assert build.warnings
    assert "N=2" in build.warnings[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_matrix.py -q`
Expected: FAIL — `cannot import name 'matrix'`

- [ ] **Step 3: Implement `keel/research/matrix.py`**

```python
"""Assemble the CSCV (T x N) matrix from ledger trials (spec §5.4).

Algorithm 2.3's two conditions on the matrix (§78.6): it must be a TRUE matrix -- same rows
for every column, observations synchronous across trials -- and the metric must be estimable
on subsamples of each column. The first is enforced here; the second is `cscv.py`'s problem.

Backfilled rows (`series_missing`) are refused by construction: §78.4's warning already came
true for them -- the sweeps that produced them destroyed the per-bar series needed to score
them. They still count toward M (spec §4.6); they just cannot be columns.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from keel.research.ledger import TrialRecord

#: §78.6: "if the investor is sensitive to values of phi < 1/10, the range of values the
#: logits can adopt must be greater than 10, and so N >> 10 is required."
MIN_RECOMMENDED_COLUMNS = 10


@dataclass(frozen=True)
class MatrixBuild:
    columns: list[list[Decimal]]
    trial_ids: list[str]
    refused: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_matrix(
    trials: Iterable[TrialRecord], session: str | None = None
) -> MatrixBuild:
    columns: list[list[Decimal]] = []
    trial_ids: list[str] = []
    refused: list[str] = []

    for trial in trials:
        if session is not None and trial.session != session:
            continue
        if trial.series_missing or not trial.per_bar_pnl:
            refused.append(trial.trial_id)
            continue
        columns.append(list(trial.per_bar_pnl))
        trial_ids.append(trial.trial_id)

    lengths = {len(column) for column in columns}
    if len(lengths) > 1:
        raise ValueError(
            f"columns are not synchronous: found lengths {sorted(lengths)}; §78.6 requires a "
            "true matrix with the same rows for every column"
        )

    warnings: list[str] = []
    if 0 < len(columns) < MIN_RECOMMENDED_COLUMNS:
        warnings.append(
            f"N={len(columns)} columns is below the recommended N >> {MIN_RECOMMENDED_COLUMNS}; "
            "the relative rank will be coarse and f(lambda) discontinuous (§78.6)"
        )
    return MatrixBuild(columns=columns, trial_ids=trial_ids, refused=refused, warnings=warnings)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/research/test_matrix.py -q`
Expected: 5 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/research/matrix.py tests/research/test_matrix.py
git commit -m "feat(research): CSCV matrix builder, refusing backfilled rows

Enforces §78.6's true-matrix condition (synchronous rows) and refuses
series_missing trials by construction -- they count toward M but cannot
be columns, because the sweeps that produced them destroyed the per-bar
series (§78.4)."
```

---

### Task 5: `keel trials pbo`

**Files:**
- Modify: `keel/cli.py` (extend the `trials` group from Task 2)
- Modify: `tests/research/test_trials_cli.py`

**Interfaces:**
- Consumes: `matrix.build_matrix`, `cscv.pbo`, `ledger.read_trials`.
- Produces: `keel trials pbo --ledger PATH [--session S] [--blocks N]`.

- [ ] **Step 1: Add the failing test**

Append to `tests/research/test_trials_cli.py`:

```python
def test_pbo_command_reports_but_never_names_a_winner(tmp_path):
    """The command prints probabilities. It must not print a winning configuration."""
    from decimal import Decimal

    from keel.research import ledger as trials_ledger

    path = tmp_path / "trials.jsonl"
    for column_index in range(6):
        drift = Decimal(column_index) / Decimal(10)
        series = [drift + (Decimal("1") if i % 2 else Decimal("-1")) for i in range(32)]
        trials_ledger.append_trial(
            path,
            trial_id=f"grid-{column_index}",
            session="grid",
            rule="turtle_breakout",
            params={"entry": 20 + column_index * 5},
            provenance="fitted",
            kind="sweep_node",
            decision="diagnostic_only",
            per_bar_pnl=series,
            timestamp=1_700_000_000,
        )

    runner = CliRunner()
    result = runner.invoke(
        cli, ["trials", "pbo", "--ledger", str(path), "--session", "grid", "--blocks", "4"]
    )
    assert result.exit_code == 0
    assert "PBO" in result.output
    assert "degradation slope" in result.output
    assert "Prob[OOS < 0]" in result.output
    assert "dominance" in result.output.lower()
    # ⛔ Strathern rail: no parameter value may be surfaced as a recommendation.
    assert "best" not in result.output.lower()
    assert "recommend" not in result.output.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/research/test_trials_cli.py::test_pbo_command_reports_but_never_names_a_winner -q`
Expected: FAIL — `No such command 'pbo'`

- [ ] **Step 3: Add the command to `keel/cli.py`**

```python
@trials_group.command("pbo")
@_LEDGER_OPTION
@click.option("--session", default=None, help="Only use columns from this session label.")
@click.option("--blocks", default=16, show_default=True, help="S: number of row blocks.")
def trials_pbo(ledger: Path | None, session: str | None, blocks: int) -> None:
    """Probability of Backtest Overfitting over a declared candidate grid (§78.6).

    Reports probabilities. It deliberately does NOT report which configuration won: PBO
    evaluates the quality of a selection process and must never become the objective that
    selection relies on (§78.7's Strathern warning).
    """
    from keel.research import cscv as cscv_module
    from keel.research import matrix as matrix_module

    trials = trials_ledger.read_trials(_ledger_path(ledger))
    build = matrix_module.build_matrix(trials, session=session)
    if not build.columns:
        raise click.ClickException("no usable columns (all trials are series_missing?)")
    for warning in build.warnings:
        click.echo(f"warning: {warning}", err=True)
    if build.refused:
        click.echo(f"refused {len(build.refused)} series_missing trial(s)", err=True)

    result = cscv_module.pbo(build.columns, s=blocks)

    click.echo(f"columns (N)          : {result.n_columns}")
    click.echo(f"blocks (S)           : {result.n_blocks}")
    click.echo(f"combinations         : {result.n_combinations}")
    click.echo(f"rows used / dropped  : {result.rows_used} / {result.rows_dropped}")
    click.echo(f"PBO                  : {result.pbo:.4f}")
    click.echo(f"degradation slope    : {result.degradation_slope:.4f}")
    click.echo(f"Prob[OOS < 0]        : {result.prob_loss:.4f}")
    click.echo(f"stochastic dominance : 1st={result.dominance_1st} 2nd={result.dominance_2nd}")
    click.echo(
        "\nRead PBO alongside the degradation slope, never alone (§78.7 limitation 4): a high "
        "PBO with a flat, positive OOS scatter is the GOOD outcome -- a broad plateau of "
        "near-identical configurations produces high PBO by construction."
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/research/test_trials_cli.py -q`
Expected: 4 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/cli.py tests/research/test_trials_cli.py
git commit -m "feat(cli): keel trials pbo

Runs CSCV over a declared candidate grid and reports PBO plus the three
free companions. Prints no winning configuration, by design; a test
asserts the output contains neither 'best' nor 'recommend'."
```

---

### Task 6: The G4 gate

**Files:**
- Modify: `packages/keel-core/keel_core/config.py` (add `ResearchConfig`, wire into `Config`)
- Modify: `config.yaml` (add the `research:` block)
- Modify: `keel/strategy/promotion.py` (add `PBOGate`, `g4_pbo_gate`)
- Modify: `tests/strategy/test_promotion.py` (append the truth table)

**Interfaces:**
- Consumes: nothing from `keel/research/` — the gate takes plain `Decimal` inputs, keeping
  `promotion.py` free of any dependency on the diagnostic machinery.
- Produces: `PBOGate(pbo_max, slope_floor)`, `g4_pbo_gate(pbo, slope, gate) -> tuple[bool, list[str]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/strategy/test_promotion.py`:

```python
# -- G4: PBO conjunction gate (spec §7) ----------------------------------------

from keel.strategy.promotion import PBOGate, g4_pbo_gate


def test_g4_passes_when_pbo_low_and_slope_shallow():
    ok, reasons = g4_pbo_gate(Decimal("0.01"), Decimal("-0.2"), PBOGate())
    assert ok is True
    assert reasons == []


def test_g4_passes_on_high_pbo_with_shallow_slope():
    """The plateau case (§78.7 limitation 4).

    A broad plateau is a set of near-identical configurations, which produces high PBO BY
    CONSTRUCTION -- and §54.10/§73.13 tell us to PREFER a broad plateau. A bare 0.05 gate
    would punish the robust choice, so the conjunction must let this through.
    """
    ok, reasons = g4_pbo_gate(Decimal("0.80"), Decimal("-0.10"), PBOGate())
    assert ok is True
    assert reasons == []


def test_g4_passes_on_steep_slope_with_low_pbo():
    ok, _ = g4_pbo_gate(Decimal("0.01"), Decimal("-0.90"), PBOGate())
    assert ok is True


def test_g4_fails_only_on_the_conjunction():
    ok, reasons = g4_pbo_gate(Decimal("0.80"), Decimal("-0.90"), PBOGate())
    assert ok is False
    assert len(reasons) == 1
    assert "0.80" in reasons[0]
    assert "-0.90" in reasons[0]


def test_g4_boundaries_are_strict_inequalities():
    # Exactly at both thresholds is a PASS: the gate fires on > and <, not >= and <=.
    ok, _ = g4_pbo_gate(Decimal("0.05"), Decimal("-0.5"), PBOGate())
    assert ok is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/strategy/test_promotion.py -q -k g4`
Expected: FAIL — `cannot import name 'PBOGate'`

- [ ] **Step 3: Add `ResearchConfig` to `packages/keel-core/keel_core/config.py`**

Add the dataclass next to the other frozen config dataclasses:

```python
@dataclass(frozen=True)
class ResearchConfig:
    """Thresholds for the G4 overfitting gate (spec §7, KB §78).

    ⛔ NEVER TUNE THESE TO OBTAIN A DESIRED VERDICT. Tuning an overfitting threshold until a
    strategy passes is precisely the Strathern misuse the gate exists to prevent (§78.7).

    `slope_floor` is calibrated from §78.8's worked cases -- real strategy -0.35, pure random
    walk -0.61, overfit real strategy -0.75 -- so -0.5 sits between the real-strategy case and
    the noise/overfit cases.
    """

    pbo_max: Decimal = Decimal("0.05")
    slope_floor: Decimal = Decimal("-0.5")
```

Add the field to `Config` (alongside `logging`):

```python
    research: ResearchConfig = field(default_factory=ResearchConfig)
```

And in the loader, next to how `logging` is parsed, add:

```python
def _parse_research(raw: dict[str, Any]) -> ResearchConfig:
    research_raw = raw.get("research") or {}
    defaults = ResearchConfig()
    return ResearchConfig(
        pbo_max=_non_negative_decimal(
            research_raw.get("pbo_max", defaults.pbo_max), "research.pbo_max"
        ),
        slope_floor=Decimal(str(research_raw.get("slope_floor", defaults.slope_floor))),
    )
```

Wire `research=_parse_research(raw)` into the `Config(...)` construction. Note `slope_floor` is
**negative**, so it must not go through `_non_negative_decimal`.

- [ ] **Step 4: Add the `research:` block to `config.yaml`**

```yaml
# G4 overfitting gate (KB §78). NEVER tune these to obtain a desired verdict -- doing so is
# the exact Strathern misuse the gate exists to prevent (§78.7). slope_floor is calibrated
# from §78.8: real strategy -0.35, random walk -0.61, overfit -0.75.
research:
  pbo_max: 0.05
  slope_floor: -0.5
```

- [ ] **Step 5: Add the gate to `keel/strategy/promotion.py`**

```python
@dataclass
class PBOGate:
    """G4 thresholds (spec §7). Defaults mirror `keel_core.config.ResearchConfig`."""

    pbo_max: Decimal = Decimal("0.05")
    slope_floor: Decimal = Decimal("-0.5")


def g4_pbo_gate(
    pbo: Decimal, degradation_slope: Decimal, gate: PBOGate | None = None
) -> tuple[bool, list[str]]:
    """G4: fail only on `pbo > pbo_max` AND `slope < slope_floor`.

    A CONJUNCTION, deliberately, not the bare scalar. §78.7's limitation 4: "it is entirely
    possible that all the N strategies have high but similar Sharpe ratios... PBO will be
    high. Here overfitting is among many 'skillful' strategies." That is this project's
    plateau case exactly, and §54.10/§73.13 direct us to PREFER a broad plateau -- so a bare
    0.05 gate would reject the robust choice. §78.7 supplies the reading rule this encodes:
    high PBO with a flat, positive OOS scatter is the good outcome; high PBO with a steeply
    negative slope is the bad one.
    """
    gate = gate or PBOGate()
    if pbo > gate.pbo_max and degradation_slope < gate.slope_floor:
        return False, [
            f"PBO {pbo:.2f} > {gate.pbo_max} AND degradation slope "
            f"{degradation_slope:.2f} < {gate.slope_floor}: the IS-best configuration "
            "underperforms the OOS median and OOS performance degrades steeply in IS "
            "performance -- the signature of a fitted, not a robust, selection (§78.8)"
        ]
    return True, []
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/strategy/test_promotion.py tests/test_config.py -q`
Expected: all pass, including the 5 new G4 tests

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/strategy/promotion.py packages/keel-core/keel_core/config.py config.yaml tests/strategy/test_promotion.py
git commit -m "feat(promotion): G4 overfitting gate on the PBO/slope conjunction

Fails only on pbo > 0.05 AND slope < -0.5, never the bare scalar. §78.7
limitation 4: a broad plateau is a set of near-identical configurations
and produces high PBO by construction -- and §54.10/§73.13 tell us to
prefer a broad plateau. Gating on the scalar alone would punish the
robust choice.

slope_floor = -0.5 is calibrated from §78.8 (real -0.35, random walk
-0.61, overfit -0.75). Both constants live in config.yaml marked never
to be tuned."
```

---

### Task 7: Report section + `keel simulate` auto-record

**Files:**
- Modify: `keel/sim/report.py` (add `_render_pbo_section`, call it from `render_markdown`)
- Modify: `keel/cli.py` (`simulate` records its run)
- Modify: `tests/sim/test_report.py`

**Interfaces:**
- Consumes: `cscv.PBOResult` (Task 3), `promotion.g4_pbo_gate` (Task 6).
- Produces: `_render_pbo_section(result, gate_ok, gate_reasons) -> list[str]`, and a new optional
  `pbo_result` keyword on `render_markdown` (backward-compatible, following the existing
  `tier_results`/`pooled_by_class` precedent in this file).

- [ ] **Step 1: Write the failing test**

Append to `tests/sim/test_report.py`:

```python
def test_pbo_section_reports_all_four_statistics_and_the_reading_rule():
    from decimal import Decimal

    from keel.research.cscv import PBOResult
    from keel.sim.report import _render_pbo_section

    result = PBOResult(
        pbo=Decimal("0.62"),
        n_combinations=12870,
        n_columns=12,
        n_blocks=16,
        rows_used=1808,
        rows_dropped=11,
        logits=[],
        is_performance=[],
        oos_performance=[],
        degradation_slope=Decimal("-0.20"),
        prob_loss=Decimal("0.30"),
        dominance_1st=False,
        dominance_2nd=True,
    )
    lines = _render_pbo_section(result, True, [])
    body = "\n".join(lines)

    assert "0.62" in body
    assert "-0.20" in body
    assert "0.30" in body
    assert "12870" in body
    # The reading rule must travel with the number (§78.7 limitation 4).
    assert "plateau" in body.lower()
    # Prob[OOS<0] is a SEPARATE failure mode from overfitting (§78.8).
    assert "separately" in body.lower() or "separate" in body.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/sim/test_report.py -q -k pbo_section`
Expected: FAIL — `cannot import name '_render_pbo_section'`

- [ ] **Step 3: Add the section to `keel/sim/report.py`**

```python
def _render_pbo_section(
    result: "PBOResult", gate_ok: bool, gate_reasons: list[str]
) -> list[str]:
    """Render the PBO block. The reading rule travels WITH the number, deliberately.

    §78.7 limitation 4 means a bare PBO is misleading on its own for exactly the plateau
    shape this project is told to prefer, so the section never prints phi without the
    degradation slope beside it and the interpretation underneath.
    """
    status = "PASS" if gate_ok else "FAIL"
    lines = [
        "## Overfitting diagnostics (PBO / CSCV)",
        "",
        f"- **PBO (phi):** {result.pbo:.4f}",
        f"- **Degradation slope:** {result.degradation_slope:.4f}",
        f"- **Prob[OOS < 0]:** {result.prob_loss:.4f}",
        f"- **Stochastic dominance:** 1st-order {result.dominance_1st}, "
        f"2nd-order {result.dominance_2nd}",
        f"- **Grid:** N={result.n_columns} columns, S={result.n_blocks} blocks, "
        f"{result.n_combinations} combinations, {result.rows_used} rows "
        f"({result.rows_dropped} oldest dropped)",
        "",
        f"**G4: {status}**",
        "",
    ]
    for reason in gate_reasons:
        lines.append(f"- {reason}")
    if gate_reasons:
        lines.append("")
    lines.extend(
        [
            "> Read PBO alongside the degradation slope, never alone (KB §78.7 limitation 4): "
            "a broad plateau of near-identical configurations produces a high PBO **by "
            "construction**, and a broad plateau is what §54.10/§73.13 tell us to prefer. "
            "High PBO with a flat, positive OOS scatter is the good outcome; high PBO with a "
            "steeply negative slope is the bad one.",
            "",
            "> `Prob[OOS < 0]` is reported **separately** from PBO on purpose (§78.8): even at "
            "phi ~ 0 it can be high, meaning OOS performance is poor for reasons other than "
            "overfitting. They are distinct failure modes.",
            "",
            "> PBO is orthogonal to look-ahead bias and fee realism (§78.7 limitation 3). It "
            "does not check whether the backtest itself is correct.",
            "",
        ]
    )
    return lines
```

Add `pbo_result: PBOResult | None = None` and `pbo_gate: tuple[bool, list[str]] | None = None` as
trailing keyword arguments to `render_markdown`, and emit the section when `pbo_result is not None`.
Import `PBOResult` under `TYPE_CHECKING` to keep `sim` free of a runtime dependency on `research`.

- [ ] **Step 4: Wire auto-recording into `keel simulate` in `keel/cli.py`**

Add two options to the existing `simulate` command:

```python
@click.option(
    "--trial-decision",
    type=click.Choice(sorted(trials_ledger.DECISIONS)),
    default="diagnostic_only",
    show_default=True,
    help="How this run counts in the trials ledger. A plain validation run of the shipped "
         "config is a diagnostic and does NOT increment N (spec §4.4).",
)
@click.option(
    "--trial-provenance",
    type=click.Choice(sorted(trials_ledger.PROVENANCE)),
    default="a_priori",
    show_default=True,
)
@click.option("--no-trial-record", is_flag=True, default=False, help="Skip ledger recording.")
```

After the simulation completes and before printing the verdict, append one row per rule using the
per-bar equity deltas the sim already produces:

```python
    if not no_trial_record:
        for rule_name, series in sim_result.per_rule_bar_pnl.items():
            trials_ledger.append_trial(
                trials_ledger.DEFAULT_LEDGER_PATH,
                trial_id=f"simulate-{rule_name}-{int(time.time())}",
                session="keel simulate",
                rule=rule_name,
                params=sim_result.rule_params.get(rule_name, {}),
                provenance=trial_provenance,
                kind="sweep_node",
                decision=trial_decision,
                per_bar_pnl=series,
                series_missing=not series,
            )
```

If `SimResult` has no `per_rule_bar_pnl`/`rule_params` attributes, add them in `portfolio_sim.py`
as plain dicts populated during the bar loop — the equity series is already tracked there.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/sim tests/research -q`
Expected: all pass

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check && uv run pytest -q
git add keel/sim/report.py keel/cli.py tests/sim/test_report.py
git commit -m "feat(report): PBO section + auto-record simulate runs to the ledger

The reading rule travels with the number: the section never prints phi
without the degradation slope beside it, because §78.7 limitation 4 makes
a bare PBO misleading for exactly the plateau shape we are told to prefer.

simulate defaults to decision=diagnostic_only, so validating the shipped
config does not inflate N (spec §4.4)."
```

---

### Task 8: Backfill the historical trials

**Files:**
- Create: `docs/experiments/trials-ledger.jsonl` (via the CLI, then committed)
- Create: `docs/experiments/2026-07-20-trials-backfill.md` (the reconstruction's audit trail)

**This task is judgment, not mechanism.** Read the sources before writing rows; do not invent
counts from memory.

- [ ] **Step 1: Read the primary sources**

```bash
ls docs/experiments/
grep -rn "config\|period\|sweep\|tested" docs/experiments/*.md | head -50
```

Every trial must be traceable to a document. The known set, each to be confirmed against its record:

| experiment | expected rows | kind |
|---|---|---|
| entry-period walk-forward (Phase A) | one per candidate lookback tested | `sweep_node` |
| rank-markets / ER diagnostic | one per Donchian period × asset combination reported | `sweep_node` |
| ADX ablation (gate on/off) | 2 | `ablation` |
| random-entry control arm | 1 | `ablation` |
| S1 profitable-trade filter (on/off) | 2 | `ablation` |
| S1+S2 ensemble | 1 | `sweep_node` |
| CTS-bucket diagnostic | 1 | `ablation` |
| confluence-gate refutation | 1 | `ablation` |
| `min_trades` 100→30, then 30→100 | 2 | `threshold_nudge` |
| the 3 refuted dip-buyer rules × 3 assets | 9 | `rule_retirement` |
| Turtle hourly→daily rebuild | 2 | `sweep_node` |

- [ ] **Step 2: Write the reconstruction note first**

Create `docs/experiments/2026-07-20-trials-backfill.md` recording, per experiment: the source
document, the number of configurations counted, and — where the count is uncertain — the range and
which end was chosen. **Where ambiguous, over-count** (spec §4.6): §78.7 is asymmetric, since hiding
trials underestimates overfit while padding with deliberate losers is a distinct abuse that
over-counting genuine uncertainty does not commit.

- [ ] **Step 3: Append the rows**

Use `keel trials record` with `--series-missing` for every row (spec §4.6 — the per-bar series is
gone), `--provenance fitted` for anything chosen by comparing our own backtests, and
`--provenance a_priori` for anything adopted from the KB without a comparison.

- [ ] **Step 4: Verify the chain and the counts**

```bash
uv run keel trials verify
uv run keel trials list | tail -5
```
Expected: `chain intact`, and an `M` matching the reconstruction note's total.

- [ ] **Step 5: Commit**

```bash
git add docs/experiments/trials-ledger.jsonl docs/experiments/2026-07-20-trials-backfill.md
git commit -m "docs(research): backfill historical trials as summary-only rows

Reconstructed from the experiment records, each row traceable to a source
document (see the backfill note). All carry series_missing: they count
toward M and MinBTL but cannot be CSCV columns, because §78.4's warning
already came true -- the sweeps that produced them destroyed the per-bar
series needed to score them.

Where a count was ambiguous the reconstruction over-counts, per spec §4.6."
```

---

## Final verification

- [ ] `uv run pytest -q` — green, test count up by ~30 from the 707 baseline
- [ ] `uv run ruff check` — clean
- [ ] `uv run keel trials verify` — chain intact
- [ ] `uv run keel trials list` — M and N_decisions both plausible
- [ ] Push the branch and open a PR

**Do not merge on a green suite alone.** The first `keel trials pbo` run against a real grid is the
acceptance test that matters, and a high PBO is the *expected* result for a rule whose 40/20 was
selected by walk-forward. The degradation slope decides whether G4 bites. Per spec §7, a G4 failure
is **information and must not be answered by relaxing a threshold**.
