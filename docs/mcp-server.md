# The read-only MCP server (`keel mcp`)

A research assistant is the third reader the browser view and the TUI never reached: no
window, no terminal, one JSON-RPC stream and a model on the other end of it. `keel mcp`
serves that reader keel's state, logs and research record — and nothing it could act on.
It cannot place, halt or release anything, and that claim is mechanically checked, not merely
promised: `tests/mcp/test_readonly.py`, written before the server existed, pins it best-effort
against naming and call-shape — honest about being a static scan, which cannot close dynamic
dispatch.

## What it exposes

Eight tools. Each delegates to the same service seams an operator's front-end calls — the
doctor gather, the #453 capability inventory, the repository reads, the trials ledger — so an
assistant and a terminal cannot be shown two different accounts of one deployment.

| Tool | What it returns |
| --------- | ----------- |
| `doctor` | The same findings `keel doctor` computes (subscription freshness, rail states, allowance headroom, recent vetoes, data health, sizing admissibility), each with the CLI fix that resolves it. |
| `capabilities` | The #453 capability inventory as rows: every action that widens what keel can do without asking again, and the gate covering it. Needs no database. |
| `profiles` | The deployment's `config*.yaml` files as structured rows (allowlist, `risk_pct`, caps, DCA budget, granularities, auto-cycle interval). A file that will not load is listed with its error. |
| `orders` | Rows from the orders audit log, filtered by mode/product/status, newest last, bounded by `limit` (default 50, max 200). |
| `veto_log` | Parsed `executor.order_vetoed` events from the engine's JSONL log since a timestamp (default the last 24h), with each event's rail violations, bounded by `limit` (default 100, max 500). |
| `purification` | The KB §65.9 income purification report: what is owed by asset, units from tainted sources, entries awaiting human classification. Report-only, like the CLI. |
| `trials` | The research trials ledger: row and decision counts, hash-chain verification errors (first 20, then a count), the most recent rows. An optional `path` is a bare file name beside the default ledger, never a filesystem path. Experiments only — money has its own ledger. |
| `reports` | The research corpora: list documents under `docs/research`, the experiments corpus or the reports corpus, or read one bounded document (bare name, never a path). |

Money fields arrive as strings — the repo's TEXT-money convention, so a number is never
rounded twice by JSON floating point. Lists are bounded, for the same reason every page the
browser view serves is bounded.

## Running it

```bash
uv run keel --db keel.db --config config.yaml mcp
```

The transport is **stdio**: one JSON-RPC 2.0 message per line in, one response per line out,
until EOF. It is a minimal read-only subset of MCP — `initialize`, `tools/list`,
`tools/call` — hand-rolled over the standard library, exactly as `keel/web/server.py` answers
HTTP with `http.server`: no `mcp` SDK, no pydantic, no asyncio, **no new dependency**. Point
any stdio MCP client at the command:

```json
{
  "mcpServers": {
    "keel": {
      "command": "keel",
      "args": ["--db", "keel.db", "--config", "config.yaml", "mcp"]
    }
  }
}
```

Behaviour a client can rely on:

- `initialize` echoes the client's `protocolVersion` and answers `serverInfo` as
  **`keel-read-only`** — the name says the whole proposition.
- Notifications (messages with no `id`) are never answered. Stdin alone is not enough to make
  this server say anything.
- A tool that fails answers as a tool **result** with `isError: true`, not a dead connection;
  a malformed line answers `-32700`; an unknown method answers `-32601`. The loop outlives
  everything a client can get wrong.
- **Stdout is protocol and nothing else.** No banner, no disclaimer footer — either would
  corrupt the stream. The read-only statement travels in `serverInfo` and the tool
  descriptions, where the client actually reads it.

## Why it cannot trade

Because the write surface does not exist, and a test — not a promise — says so.
`tests/mcp/test_readonly.py` was committed **before** `keel/mcp/` existed, so the server was
born inside the fence rather than moved into one later. Six walls:

1. **A write-verb vocabulary.** Every tool name and description is checked, word-boundary
   matched against normalized tokens (underscores inserted at lower→Upper camelCase
   boundaries, then split on hyphens/underscores/spaces — so `armAutonomy`, `re-arm` and
   `arm_now` all expose the token `arm`), against `arm`, `release`, `resume`, `spend`,
   `attest`, `promote`, `update`, `reset`, `record`, `withdraw`, `autonomy`, `kill`, `trade`,
   `execute`, `order_create`, `submit`, `place`. A description is what a model reads before
   choosing a tool; it must not advertise a write.
2. **A registry mapping.** No `keel/mcp/*.py` references any #453 capability row's
   `module.function` — the gated actions are unreachable by name, not merely uncalled. And
   the vocabulary is defined independently of the registry, then cross-checked against every
   row, because the registry only inventories **gated** mutators: `keel subscription attest`
   and `keel rules promote --force` mutate with no gate and no row, and the vocabulary catches
   them anyway.
3. **An AST write-deny scan.** No call on any object whose attribute starts with a write-ish
   prefix (`set_`, `upsert_`, `record_`, `insert_`, `arm`, `attest`, `execute`, ...). The
   allowlist is **empty, and pinned empty** — a read-only server has no legitimate write call
   to allow, so adding one requires a written argument in review, not an accident in a diff.
   One narrow exemption, not an allowlist entry: `.execute("PRAGMA ...")` with a constant
   literal is connection configuration, never DML — it exists because the package must run
   `PRAGMA query_only = ON`, the engine-level read-only enforcement itself.
4. **No gate call sites.** `_require_interactive_confirmation` appears nowhere in the package.
   A server must never hold the ceremony gate: its fail-closed property is for terminals, and
   a pipe-connected process borrowing it is how a cron job would come to look like a human.
5. **A docs pin.** The table above names exactly the tools the server exposes,
   bidirectionally — a documented tool that does not exist, or an exposed tool the docs hide,
   both fail CI.
6. **A closed import surface.** `keel.mcp.server` imports without click and without
   `keel.cli`; the package imports neither the executor nor the agent. The trading paths
   start unavailable, not merely unused.

And the runtime shape matches: every tool opens its own database connection **without
`migrate`** (the `keel/web/server.py` rule — a read-only view must not take a schema write
lock against a database the agent may be mid-cycle on), drops it when done, and **refuses to
open a database that does not exist** rather than letting `sqlite3.connect` create one.
Stated honestly, opening does carry one file-level side effect: connecting flips a
rollback-journal database to WAL — identical to the existing keel web surface, which connects
the same way — while `PRAGMA query_only = ON` on the connection makes row writes impossible
at the engine level.
Doctor's gather — the one seam shared with the CLI — is pinned read-only at the SQLite level
by a test that counts `total_changes` around a call.

## What it will never expose

Credentials, `.env` contents, anything from the OS keychain, and any tool that could arm,
release, spend, attest, promote or halt anything. Those stay behind the interactive-terminal
gate, where #436 put them. If a future tool needs to write, the answer is not a hole in this
fence; it is a new gate with its own inventory row — and this page will say so.
