# Release packaging & bootstrap — design

**Date:** 2026-07-21
**Status:** Approved design (pending user review)
**Workstream:** B of three (see the decomposition below). Items 4, 5, 6 of the 2026-07-21 requirements.

## Context

`keel` is a live-money spot-crypto trading agent. Its release path is deliberately manual and
human-gated (`.github/workflows/release.yml`, `docs/RELEASING.md`): nothing that can move money
ships on a merge. A release runs tests+ruff, stamps a build-identity hash, builds wheels for the
workspace (`keel` + `keel-core` + `keel-broker-*`), verifies the artifact self-identifies as a
clean `[release]`, tags `v<version>`, and publishes a GitHub Release with the wheels attached and
auto-generated notes.

Three requirements refine that path. This spec covers them as one workstream because they all
answer "what does a release ship, and how does a fresh deployment come up correctly?"

The 2026-07-21 requirement list decomposed into three workstreams; this is **B**:

| | Workstream | Items |
|---|---|---|
| A | Security simplification (drop vault + passphrase; autonomous = profile flag) | 1, 2 |
| **B** | **Release packaging & bootstrap (this spec)** | **4, 5, 6** |
| C | Asset sourcing & vetting (fetch Coinbase holdings → screen gate) | 3 |

A and C are **out of scope here** and get their own spec → plan → implementation cycles.

## Goals

1. **Item 4 — Self-contained release notes.** Release notes carry each merged PR's *body content*,
   not just its title and a link, so a reader never has to click through to know what changed.
2. **Item 5 — A production config as a release asset.** The GitHub Release ships a `config.yaml`
   shaped for real use (real allowlist, sensible caps) in `mode: confirm` — ready for live, but
   never live-on-download. The in-wheel dev template stays `mode: paper`.
3. **Item 6 — Seed + migration lifecycle.** A fresh deployment seeds the strategy (rules) library;
   an existing database is migrated by a dedicated, idempotent `keel migrate` command; a manually
   dispatchable GitHub workflow exercises/dispatches migration, structured for the server future
   without pretending to migrate a database that does not yet exist.

## Non-goals

- No coupling of migration to a hosted/server database from release CI — there is no server DB
  target yet (that arrives with workstream A's "hosted future"). We build the *plumbing and a
  manual workflow*, not a live CI-against-prod step.
- No change to the money-safety model: seeded rules stay `candidate` (cannot trade until a
  deliberate `rules promote`); the released config stays `mode: confirm`.
- No change to versioning, the build-identity stamp, or the wheel-by-path install guarantees.

---

## Design

### 3.1 Release notes from PR bodies (item 4)

**Problem.** `release.yml`'s "Compose release notes" step calls the GitHub
`releases/generate-notes` API, which returns a flat/categorised list of `* <title> by @author in
#N` — titles and links only. We want each PR's cleaned body inlined, grouped by the same labels.

**Approach — a tested pure function + thin fetch glue.** The categorisation and body-cleaning is
logic worth testing; the GitHub fetching is I/O. Split them:

- **`scripts/release_notes.py`** — a repo-level (not shipped in the wheel) module with a pure
  function:

  ```python
  def compose_release_notes(prs: list[PullRequest], *, categories: list[Category]) -> str: ...
  ```

  where `PullRequest` is a small dataclass `{number, title, body, labels}` and `Category` mirrors
  `.github/release.yml` (`title`, `labels`, with `"*"` catch-all). It:
  1. Drops any PR labelled `norelease`.
  2. Assigns each PR to the **first** category whose labels intersect the PR's labels; unlabelled /
     unmatched PRs fall to the `"*"` catch-all ("Other changes"), exactly like today's grouping.
  3. Renders, per category with ≥1 PR: an `##` section heading, then per PR `### <title> (#N)`
     followed by the **cleaned body**.
  4. **Body cleaning** (`clean_pr_body`): strip the `🤖 Generated with [Claude Code]…` footer and
     everything after it; strip `<!-- … -->` HTML comments; strip trailing `Co-Authored-By:` lines;
     collapse 3+ blank lines to one; trim. An empty cleaned body renders as `_(no description)_`.

  A `__main__` guard reads a JSON array of PRs from stdin and prints the composed notes, so the
  workflow can pipe to it. `.github/release.yml` stays the single source of truth for categories;
  the workflow passes it (parsed) so the mapping is not duplicated in Python.

- **`release.yml` change.** Replace the `generate-notes` call. Gather the PRs merged in
  `PREV..HEAD`:
  1. `git log --format=%H <PREV>..HEAD` (or from start of history on the first release).
  2. For each commit, `gh api repos/${repo}/commits/<sha>/pulls -q '.[].number'`; collect the
     unique PR numbers.
  3. For each number, `gh api repos/${repo}/pulls/<n> -q '{number,title,body,labels:[.labels[].name]}'`.
  4. Pipe the JSON array of PRs into `python scripts/release_notes.py`, which emits the grouped,
     body-inlined markdown.

  The fixed preamble (build-from hash, install-by-path warning) is unchanged and still prepended.

**Why a repo-level script, not a `keel/` module:** it is release tooling, never runtime; shipping
it in the wheel would bloat the artifact. Tests import it directly from `scripts/`.

### 3.2 `config.yaml` as a live release asset (item 5)

**Two config files, by intent.**

- **`keel/templates/config.yaml`** (unchanged) — the **dev** template: `mode: paper`, shipped
  inside the wheel (`pyproject` `artifacts = ["keel/templates/*.yaml"]`), written by
  `keel init-config` / `keel init`. Paper mode places nothing.
- **`keel/templates/config.live.yaml`** (new, committed, reviewed) — the **production** template:
  identical shape, but `auto_trade.mode: confirm`, real allowlist (`BTC`, `ETH`, `PAXG`), sensible
  caps / `history_days`, and header comments naming exactly what to review before going live
  (secrets in `.env`, caps, allowance, promoting rules). It is glob-matched by the existing
  `artifacts` line, so it also ships in the wheel — enabling a local reproduction of the release
  asset.

**Release attachment.** In `release.yml`'s publish step, copy the live template to a
download-friendly name and attach it:

```
cp keel/templates/config.live.yaml config.yaml
gh release create "v<version>" dist/* config.yaml --title … --notes-file …
```

So the Release lists all wheels **plus** `config.yaml` (the production, confirm-mode config).

**Local parity.** `keel init-config` gains a `--live` flag that writes `config.live.yaml`'s
contents instead of the dev template, so an operator can reproduce the exact release asset without
downloading it. Default (no flag) is unchanged (dev/paper).

**Safety.** The production config is `mode: confirm` — the tool asks before every order. Going
autonomous remains a separate, deliberate edit (and, after workstream A, a tracked profile choice).
Nothing ships armed. A release-time check asserts the live template parses via `load_config` and is
`mode: confirm` (a red tripwire against accidentally committing an armed config).

### 3.3 Seed + migration lifecycle (item 6)

**Two distinct operations, never conflated:**

- **Seed** = populate the strategy library on a *fresh* deployment. In this codebase the `rules`
  table *is* the strategy library (`keel rules seed` inserts one `candidate` per (kind, product)
  from each rule's constructor defaults). Seeding is first-run data, idempotent by (kind,
  product_id). Seeded rules are `candidate` — they cannot trade until a deliberate `rules promote`.
- **Migrate** = evolve the *schema* of an *existing* database. `keel/data/db.py::migrate(conn)`
  already does this incrementally against a `schema_version` table (`SCHEMA_VERSION = 6`), each step
  guarded and idempotent. **Migration must never re-seed** — that would resurrect deleted/refuted
  rules.

**New: `keel migrate` command.** A thin, idempotent CLI wrapper over `db.migrate()`:
- Reads the current `schema_version` (0 if absent/fresh), calls `db.migrate(conn)`, reports
  `migrated <from> -> <to>` (or `already at <SCHEMA_VERSION>, nothing to do`).
- No network, no authz gate, no seeding — schema only. Safe to run repeatedly and on a live DB.
- `--db` targets an explicit database path (defaults to the context `db_path`), so it can be
  pointed at wherever the database lives — including a future server-mounted path.

**Fresh-deploy seeding.** `keel init` already does `init-config` + `rules seed` (candidates). We
make the bootstrap explicit and release-documented: `keel init` first ensures the schema exists
(runs `db.migrate` on the fresh DB, which `_open_repo` already does on connect), then seeds the
candidate rule library. No behaviour change beyond documenting it as *the* fresh-deploy path in
`docs/RELEASING.md`. Seeding stays idempotent, so re-running `init` on an existing deployment
adds nothing.

**Manual migration workflow — `.github/workflows/migrate.yml`.** `workflow_dispatch` only (never
on push/merge). Honest scaffold for the server future, useful today as a migration-integrity check:
- Input `db_path` (optional, default empty).
- Install via `uv sync`.
- **If `db_path` is provided** (future self-hosted-runner / server-mounted DB): run
  `keel migrate --db "<db_path>"` and print the from→to report.
- **If `db_path` is empty** (today's default): run a **migration smoke test** — build a fresh DB
  and a synthetic "old" DB (stamped at an earlier `schema_version`), run `db.migrate`, and assert
  both reach `SCHEMA_VERSION`. This gives the workflow a real job now (catches a broken migration
  chain) without inventing a production DB that does not exist.
- A comment in the file documents the deferred seam: release CI will call this with the server's
  DB target once workstream A stands the server up. Release CI is **not** wired to it in this spec.

---

## Components & interfaces (files touched)

| File | Change |
|---|---|
| `scripts/release_notes.py` | **new** — pure `compose_release_notes` + `clean_pr_body`; `__main__` stdin→stdout glue |
| `tests/test_release_notes.py` | **new** — grouping, catch-all, `norelease` exclusion, body cleaning, empty-body |
| `.github/workflows/release.yml` | replace `generate-notes` step with PR-fetch + `scripts/release_notes.py`; attach `config.yaml`; add live-config parse/mode tripwire |
| `keel/templates/config.live.yaml` | **new** — production template, `mode: confirm`, real allowlist/caps, review-before-live header |
| `keel/cli.py` | `init-config --live` flag; **new** `keel migrate` command |
| `tests/test_cli_*.py` | `--live` writes the live template; `keel migrate` reports from→to and is idempotent |
| `.github/workflows/migrate.yml` | **new** — `workflow_dispatch`; `db_path` target or migration smoke test |
| `docs/RELEASING.md` | document the config asset, the fresh-deploy seed path, and `keel migrate` + the workflow |
| `keel/templates/config.yaml` | unchanged (stays dev/paper) |

## Data flow

- **Release notes:** `git log PREV..HEAD` → per-commit `gh api …/pulls` → unique PR numbers → per-PR
  `gh api …/pulls/<n>` (title/body/labels) → JSON → `scripts/release_notes.py` → grouped markdown →
  prepend preamble → `gh release create --notes-file`.
- **Config asset:** committed `config.live.yaml` → wheel (via `artifacts` glob) *and* → copied to
  `config.yaml` → attached to the Release.
- **Fresh deploy:** `keel init` → `db.migrate` (fresh schema) + `rules seed` (candidate library) →
  operator edits `config.yaml` + `.env`, promotes rules deliberately.
- **Existing deploy:** `keel migrate` (or the workflow) → `db.migrate` runs outstanding steps only.

## Error handling & safety

- **No red build ships:** the existing tests+ruff gate is unchanged; `scripts/release_notes.py` has
  its own unit tests run by that gate.
- **Live-config tripwire:** the release asserts `config.live.yaml` parses and is `mode: confirm`;
  a committed armed config fails the release loudly.
- **Migration never seeds; seeding never migrates schema beyond `db.migrate`'s idempotent DDL.**
  `keel migrate` is safe on a live DB (idempotent, schema-only). Seeded rules are `candidate`.
- **Empty PR body** renders `_(no description)_` rather than a blank entry.
- **First release** (no previous tag) composes notes from the start of history, as today.

## Testing

- `test_release_notes.py`: category assignment incl. first-match-wins and `"*"` catch-all;
  `norelease` dropped; footer/HTML-comment/`Co-Authored-By` stripping; blank-line collapse;
  empty-body placeholder; a full end-to-end compose over a small fixture PR set.
- CLI tests: `init-config --live` writes the live template (asserts `mode: confirm`); `keel migrate`
  on a fresh DB reports up-to-date, on a synthetic-old DB reports from→to and reaches
  `SCHEMA_VERSION`, and is idempotent on a second run.
- The migrate workflow's smoke-test logic is exercised by the same synthetic-old-DB unit test, so CI
  green ⇒ the workflow's default path is green.
- Manual/CI: full test suite + ruff stay green; the release workflow changes are validated on the
  next real release cut (the notes composition can be dry-run locally by piping recorded PR JSON).

## Future seams (explicitly deferred)

- Release-CI-triggered migration against a **server** database (needs workstream A's hosting +
  a DB target + secrets). `migrate.yml` is shaped to accept that target via `db_path`.
- Autonomous/profile mode (workstream A) will change `mode`'s vocabulary; the live config's
  `mode: confirm` remains the safe default across that change.
