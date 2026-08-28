# 0002 — Persistence is SQLite, one writer per file, until a named trigger fires

Date: 2026-08-27 · Issue: #526 (raised in the SE walkthrough, `docs/presentations/se.md` §9.6 —
"SQLite single-writer: where does that stop being enough?") · Status: decided

## Context

Persistence is SQLite in WAL mode behind a hand-rolled `Repository` (`keel/data/repository.py`)
— no ORM, plain SQL, hand-written mapping. #526 is not a proposal to change that. It asks that
the reasoning be recorded durably, with the triggers that would genuinely reopen it, so the
question stops being re-litigated by each new reader.

The shape, with the deployment's numbers (#526):

- **One writer per file.** Three profiles run — live, paper-daily, paper-hourly — but each has
  its OWN database file, so each file still has exactly one writer. The live wrapper trades once
  per UTC day; the hourly evidence profile cycles hourly. Neither is remotely write-bound.
- **The agent is synchronous by design.** One account, one venue, one cycle per interval, one
  process. The walkthrough's answer to "why not async?" (§10, quoted in #526) — concurrency
  buys nothing here and costs testability — answers "why not a database server?" in the same
  breath.
- **The audit trail is the schema.** Every order, veto and state transition is a row the engine
  reads back to make its next decision; hand-written SQL keeps that inspectable.
- **The database is a file.** It can be copied, diffed, attached to a bug report, and backed up
  before a migration — which `keel update` does automatically, per database, as
  `<db>.bak-before-<version>-<stamp>` (`keel/commands/update.py`).
- **The one number that grows:** the deployment's `keel.db` is ~129MB, mostly candles, and grows
  with every fetched product. What to watch on it is READ latency (the median-volume statistic,
  backtests), not write contention.

WAL itself already carries a measured rationale, at length, in `keel/data/db.py::connect` ("not
a tuning preference"): under a 5s dashboard poll a rollback journal died at 45s with 31,709
candles ("disk I/O error"), while WAL ran 150s to 108,501 candles under a 0.2s poll (#437). That
comment is the precedent for how this decision is recorded — measured, with the trigger named —
and it bounds what WAL buys: concurrent READERS with one writer. It does not make two writers
safe.

## Options

**A — Decide it stays, and record what would reopen it.** SQLite remains the persistence layer;
one writer per file is stated as load-bearing; four named triggers — each with the measurement
it demands — bound the question.

**B — Move to a client/server database (Postgres or similar) now.** Adopt one pre-emptively,
because a tool that moves real money "should" run on a serious database.

## Decision

**A. SQLite stays, one writer per file — and the synchronous one-writer design is load-bearing,
not a limitation waiting to be engineered away.**

The issue's own reasoning, which is the reasoning:

- B buys concurrency the design deliberately refuses (one account, one venue, one cycle per
  interval — a second actor in the write path is a second thing to reproduce in every test),
  and it pays with the file's properties: copy, diff, attach to a bug report, back up before a
  migration. "Postgres feels more serious" is not an engineering answer; it is a vibe with a
  port number.
- The three facts that make SQLite right today are not accidental: one writer; the audit trail
  IS the schema; the database is a file.

**Standing rules this decision fixes:**

1. **One writer per database file.** A supervisor, web layer, or second agent may READ (that is
   what WAL bought, #437); none may WRITE. Any design that needs a second writer has hit
   Trigger 1 and starts at that trigger's measurement — not at a migration.
2. **A fired trigger is answered with a measurement,** not by reaching for a bigger database.
   The measurement either bounds the problem inside SQLite (then the trigger closes) or shows a
   wall SQLite cannot meet (then this record is superseded).
3. The four triggers below are the complete set. A new trigger is a new decision record, not an
   edit to this one.

## The triggers

| # | What fires it | The measurement that answers it | Who observes it |
|---|---------------|---------------------------------|-----------------|
| 1 | A second process needs to **write** one database file — a supervisor alongside the agent, or a web layer that writes | Actual write contention on the real file, both writers live over a representative window: `busy_timeout` waits, `SQLITE_BUSY` count, ms spent blocked | Whoever proposes the process-model change — `db.py::connect`'s WAL note points here |
| 2 | A deployment profile moves to a cycle faster than hourly (sub-minute, per-tick persistence) | At the proposed cadence: writes/sec, WAL checkpoint frequency, and the share of cycle wall-time spent persisting | The profile's author — cadence is a config choice (`config.paper-hourly.yaml` and kin) |
| 3 | A migration on the candle tables takes long enough to threaten a cycle window | Migration wall-time on a copy of the production-sized candle table (~129MB, growing) vs the profile's cycle window — 3,600s for hourly, a day for live | The operator running `keel update` — `keel migrate` runs per database |
| 4 | Read latency on the liquidity (median-volume) or backtest paths becomes a MEASURED complaint | p50/p95 latency of the named queries against the deployment's own `keel.db`, before and after any candidate fix | Whoever timed it — the operator or researcher holding the numbers; a suspicion is not a trigger |

The first response to every trigger is the measurement in its row. Trigger 1's answer starts
"measure the actual write contention", not "migrate"; trigger 3's starts "time the migration on
a copy", not "switch engines". Only a measured wall reopens the database question.

## Consequences

- **No supervisor process and no write-path web layer against a live database file** until
  Trigger 1 fires and is answered. Read-only surfaces remain welcome — WAL exists for them.
- **No sub-hourly deployment profile** without Trigger 2's measurement on the table first.
- **Migrations stay per-database and backup-first.** The `.bak-before-*` copy is why a schema
  change over a 129MB candle table is calm; that property is part of this decision, not an
  accident of it.
- **Candle read latency is the watch item,** and it is watched with a timer, not a feeling
  (Trigger 4).
- **If a trigger fires and its measurement shows SQLite no longer fits,** the answer is a new
  decision record superseding this one — never a quiet drift onto a server "because scale".
