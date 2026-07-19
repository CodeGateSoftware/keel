# keel — Per-Order Decision Attribution and Rationale

**Status:** design, approved in brainstorming 2026-07-19.
**Sequencing:** builds on `2026-07-19-keel-trade-outcomes-and-streak-breaker-design.md`, which takes
`SCHEMA_VERSION` 2 → 3. This spec takes **3 → 4** and must be implemented after it.

## 1. Why this exists

Every order keel places should answer two questions afterwards: **which strategy produced it**, and
**why that strategy fired at that moment**. Today it answers neither.

- **Live orders carry no attribution at all.** `executor.py:404` inserts every order with a hardcoded
  `rule_id=None`. Paper mode smuggles the rule name into `raw_response`; live mode does not. This
  inconsistency is already documented as a wart in `agent.py`'s module docstring.
- **The rationale is computed and then discarded.** `Signal` carries `rule_name`, `cts_score`,
  `entry_technique` and `setup`, and `Setup.context`'s own docstring says it holds *"the indicator
  values that produced this setup, used both for explainability and as the CTS scorer's input."* All
  of it reaches `executor.execute()` and none of it is persisted.

The purpose is twofold and the two pull differently: **audit** wants a complete, durable, readable
record of why money moved; **continuous evaluation** wants structure that can be grouped, filtered and
joined against outcomes. This design serves both from one record.

## 2. Scope — decisions, not just orders (approved)

The recorded entity is an **order decision**: every intent that reached `guards.check`, whether or not
it became an order.

Placed-only would have been the narrower reading of the request, but the refusals are the more
valuable evaluation signal, and they are currently unrecoverable:

- **Vetoed by a rail** — `ExecutionResult.vetoed_by` already names the exact rails. "Rail 2 blocked
  40% of this rule's signals" is a tuning insight that leaves no trace today.
- **Signal fired, nothing placed** — in confirm mode with `confirm_fn=None` every intent is previewed
  and logged but never placed. Real decisions, real rationale, no record.

**Explicitly out of scope: no-signal cycles.** Recording every rule × product × cycle evaluation would
be a row per cycle forever, and `keel simulate` already derives the equivalent (`idle_through_move`)
from candles when the question is asked. Rows exist only when a rule actually produced a signal, which
is what bounds this table in practice.

## 3. Storage

New table, `SCHEMA_VERSION` **3 → 4**, using the versioned-migration machinery.

```sql
CREATE TABLE IF NOT EXISTS order_decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               INTEGER NOT NULL,
    product_id       TEXT NOT NULL,
    side             TEXT NOT NULL,
    order_id         INTEGER,          -- NULL when vetoed; see below
    rule_name        TEXT NOT NULL,
    cts_score        INTEGER,
    entry_technique  TEXT,
    entry            TEXT,
    stop             TEXT,
    target           TEXT,
    notional         TEXT,
    is_dca           INTEGER NOT NULL,
    disposition      TEXT NOT NULL,    -- placed | vetoed | not_placed
    reason           TEXT NOT NULL,    -- ExecutionResult.reason, verbatim
    vetoed_by        TEXT,             -- JSON array of rail names, NULL when not vetoed
    context          TEXT NOT NULL,    -- JSON: the indicator snapshot from Setup.context
    FOREIGN KEY (order_id) REFERENCES orders(id)
);
CREATE INDEX IF NOT EXISTS idx_order_decisions_ts ON order_decisions(ts);
CREATE INDEX IF NOT EXISTS idx_order_decisions_rule ON order_decisions(rule_name);
```

**`order_id` is nullable, and that is load-bearing.** A vetoed intent returns at `executor.py:288`,
*before* `insert_order` at line 332 — there is no `orders` row to reference. A `NOT NULL` column here
would silently force the design back to placed-only.

**`context` is JSON, not columns.** Its shape varies by rule: `turtle_breakout` records Donchian
levels and ADX; `pullback_continuation` records the EMA fan, the entry zone and the pattern matched.
Columns would either be mostly-NULL or force a schema change per new rule.

**`disposition` distinguishes three outcomes currently indistinguishable from outside the executor:**
`placed`, `vetoed` (rails refused), and `not_placed` (confirm mode with no `confirm_fn`, or a
broker-side failure). Without it, "no order appeared" collapses three very different situations into
one.

**`reason` disambiguates within a disposition.** `not_placed` covers two genuinely different causes —
a confirm-gate refusal and a broker rejection — and `disposition` alone cannot tell them apart.
`ExecutionResult.reason` already carries that text, so it is stored verbatim rather than re-derived.
Evaluation groups by `disposition`; audit reads `reason`.

Money and prices are exact `TEXT` (`str(Decimal)`), matching `orders`/`candles`/`trade_outcomes`.
Never `float`.

**Repository surface:** `insert_order_decision(decision: dict) -> int`,
`get_order_decisions(since_ts: int | None = None, rule_name: str | None = None) -> list[dict]`.

## 4. Write point

**One write, inside `executor.execute()`.** That function is the single choke point both paths pass
through: the veto return (~line 288) and the success return (~line 386).

It deliberately does **not** live in `agent.py`. `execute()` is also reached from `scale_out`,
`handle_oco_fill`, `roll_to_break_even` and `trail_stop_atr`; attributing at the agent level would
miss those or double-count them.

**Also fixed here:** `rule_id=None` at `executor.py:404` becomes the real rule id, so `orders` itself
carries attribution and the paper/live inconsistency ends. The decision record is the rich account;
`orders.rule_id` is the cheap join key.

## 5. Logs

Per monorepo spec §10.3 — *"Telemetry is not the audit trail… Conflating them is very hard to undo"* —
the split is:

- **The table is the audit record**: complete, durable, joinable.
- **The log is operational**: one compact `log_event` per decision carrying `decision_id`, `rule`,
  `product`, `cts_score`, `disposition`, and `vetoed_by` when present.

The indicator snapshot is **not** logged. It is already durably stored, log files rotate, and the same
rationale living in two places that can disagree once one ages out is precisely what §10.3 warns
against.

Event names: `execution.decision_placed`, `execution.decision_vetoed`, `execution.decision_not_placed`
— stable identifiers, not interpolated sentences (§10.2).

## 6. Narrative — derived, never stored

`keel_core.rationale.render_rationale(decision: dict) -> str` renders the human sentence from the
stored fields at read time:

> `turtle_breakout bought BTC-USD at 50000 (stop 49000, target 53000, CTS 8, entry: limit) — Donchian
> 20-high broken, ADX 31 confirms trend.`

Stored prose would drift from the data it describes the moment either changes. Deriving it also keeps
`keel_core` stdlib-only and pure — no I/O, trivially testable.

## 7. Testing

- A **vetoed** decision writes a row with `order_id IS NULL`, `disposition='vetoed'`, and `vetoed_by`
  naming the exact rails from `GuardResult.violations`.
- A **placed** decision writes a row with `order_id` set and `vetoed_by` NULL.
- A **not_placed** decision (confirm mode, no `confirm_fn`) is distinguishable from both.
- `context` round-trips exactly, including `Decimal` values.
- `render_rationale` produces its sentence from fields only — a test asserts no prose column exists.
- **Negative:** an intent that never reaches `guards.check` writes no row; and a run producing no
  signal writes no rows at all (the §2 scope boundary).
- **DCA is recorded like any other decision.** This is attribution, not a rail — the exemptions in
  rails 8/11/16 do not apply, and a test pins that a DCA buy produces a decision row.

Every positive assertion is paired with its negative, per the carried-forward review rule: a test that
only asserts a row appears would pass against a writer that records everything indiscriminately.

## 8. Out of scope

- Retention or pruning. The table grows unbounded, though only when a rule actually fires — small at
  this cadence. **Named rather than silently assumed**; revisit if row counts become material.
- Backfilling attribution for historical orders. It cannot be recovered — the rationale was never
  computed for those, and inventing it would fabricate an audit trail, which is worse than a gap.
- Any change to how rules score or decide. This records decisions; it does not influence them.
- A CLI or report over the table. The data lands first; surfacing it is a separate piece of work.

## 9. Done criteria

- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy` pass; baseline fixture byte-unchanged.
- Every intent reaching `guards.check` produces exactly one `order_decisions` row.
- A vetoed decision is recorded with `order_id IS NULL` and the exact rail names.
- `orders.rule_id` is populated for live orders — no longer hardcoded `None`.
- The indicator snapshot round-trips and is queryable by `rule_name`.
- One compact log event per decision; the snapshot appears in the DB only, never in logs.
- `render_rationale` is pure, lives in `keel_core`, and no prose is persisted.
