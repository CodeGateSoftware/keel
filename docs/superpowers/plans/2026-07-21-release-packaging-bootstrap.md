# Release packaging & bootstrap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship workstream B — self-contained PR-body release notes, a confirm-mode production `config.yaml` release asset, and a seed/migrate bootstrap lifecycle with a manually dispatchable migration workflow.

**Architecture:** A tested pure module (`scripts/release_notes.py`) composes notes from PR JSON; `release.yml` fetches PRs and pipes to it, and attaches a committed `config.live.yaml`. A new `keel migrate` CLI wraps the existing idempotent `db.migrate`; `init-config --live` writes the production template; a `workflow_dispatch` `migrate.yml` targets a `db_path` or runs a migration smoke test.

**Tech Stack:** Python 3.12, click, PyYAML (already a dep via config loading), pytest, GitHub Actions, `gh`/`jq`.

## Global Constraints

- Seeded rules stay `candidate`; production config stays `auto_trade.mode: confirm`. Nothing ships armed.
- `keel migrate` is schema-only and idempotent — it never seeds and never places orders.
- Release tooling (`scripts/*`) is NOT shipped in the wheel.
- Full suite green + `uv run ruff check keel tests packages` clean before each commit.
- `.github/release.yml` category order/labels are the single source of truth for grouping.

---

### Task 1: `scripts/release_notes.py` — PR-body note composition

**Files:**
- Create: `scripts/release_notes.py`, `scripts/__init__.py`
- Test: `tests/test_release_notes.py`

**Interfaces:**
- Produces: `PullRequest(number:int, title:str, body:str, labels:tuple[str,...])`; `Category(title:str, labels:tuple[str,...])`; `clean_pr_body(body:str)->str`; `categorize(prs, categories)->list[tuple[Category,list[PullRequest]]]`; `compose_release_notes(prs, categories)->str`; `load_categories(path)->list[Category]`; `DEFAULT_CATEGORIES`.

- [ ] **Step 1:** Write failing tests covering: `norelease` exclusion; first-match-wins category assignment; `"*"` catch-all; footer/HTML-comment/`Co-Authored-By` stripping; blank-line collapse; empty body → `_(no description)_`; `load_categories` against the real `.github/release.yml`.
- [ ] **Step 2:** Run `uv run pytest tests/test_release_notes.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement the module (dataclasses, regex cleaners, categorize, compose, `load_categories` via yaml, `__main__` reads a JSON array of PRs on stdin and prints notes using `load_categories(".github/release.yml")`).
- [ ] **Step 4:** Run tests → PASS; `ruff check`.
- [ ] **Step 5:** Commit `feat(release): compose release notes from PR bodies`.

### Task 2: `keel migrate` command

**Files:**
- Modify: `keel/cli.py` (new `migrate` command near `init`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `keel.data.db.{connect, migrate, SCHEMA_VERSION}`, `ctx.obj["db_path"]`.
- Produces: CLI `keel migrate [--db PATH]` printing `migrated <path>: schema <from> -> <to>` or `<path>: already at schema <n>, nothing to do`.

- [ ] **Step 1:** Write failing tests: fresh DB → `0 -> SCHEMA_VERSION`; second run → `already at`; downgraded (`UPDATE schema_version SET version=1`) → `1 -> SCHEMA_VERSION`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Add `migrate_cmd` with a `_current_schema_version(conn)` helper (0 when the table is absent), `--db` defaulting to `ctx.obj["db_path"]`.
- [ ] **Step 4:** Run → PASS; ruff.
- [ ] **Step 5:** Commit `feat(cli): keel migrate -- idempotent schema-only migration`.

### Task 3: `config.live.yaml` + `init-config --live`

**Files:**
- Create: `keel/templates/config.live.yaml`
- Modify: `keel/cli.py` (`_template_config_text(live=False)`, `init-config --live`)
- Test: `tests/test_init_and_seed.py` (append)

**Interfaces:**
- Produces: CLI `keel init-config --live` writes the production template; parses via `load_config` as `mode == "confirm"`.

- [ ] **Step 1:** Write failing tests: `--live` writes a file that `load_config` reads as `auto_trade.mode == "confirm"`; default (no flag) stays `paper`; the shipped `config.live.yaml` parses and is `confirm`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Create `config.live.yaml` (dev template with a production header + `mode: confirm`); add `live` param to `_template_config_text` and a `--live` flag to `init-config`.
- [ ] **Step 4:** Run → PASS; ruff.
- [ ] **Step 5:** Commit `feat(cli): ship a confirm-mode production config template (--live)`.

### Task 4: `scripts/migration_smoke.py`

**Files:**
- Create: `scripts/migration_smoke.py`
- Test: `tests/test_release_notes.py` or a new `tests/test_migration_smoke.py`

**Interfaces:**
- Produces: `main()` that asserts a fresh and a downgraded DB both reach `SCHEMA_VERSION`; exits 0 on success.

- [ ] **Step 1:** Write a failing test importing `scripts.migration_smoke.main` and asserting it runs without raising.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `main()` (tempfile fresh DB → migrate → assert; downgrade to 1 → migrate → assert; cleanup).
- [ ] **Step 4:** Run → PASS; ruff.
- [ ] **Step 5:** Commit `feat(release): migration smoke test for the migrate workflow`.

### Task 5: `release.yml` — PR-body notes + config asset + live tripwire

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1:** Replace the "Compose release notes" step: derive `PREV`, collect unique PR numbers across `git log $RANGE` via `gh api commits/<sha>/pulls`, fetch each PR's `{number,title,body,labels}`, `jq -s` into an array, pipe to `python scripts/release_notes.py`; keep the fixed preamble.
- [ ] **Step 2:** Add a "Verify the live config asset" step: `uv run python` asserts `load_config("keel/templates/config.live.yaml").auto_trade.mode == "confirm"`, then `cp keel/templates/config.live.yaml config.yaml`.
- [ ] **Step 3:** Extend the publish step to attach `config.yaml`: `gh release create "v$V" dist/* config.yaml …`.
- [ ] **Step 4:** `actionlint` if available / manual YAML sanity (`python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/release.yml').read_text())"`).
- [ ] **Step 5:** Commit `feat(release): inline PR bodies + attach the confirm-mode config asset`.

### Task 6: `migrate.yml` workflow + `docs/RELEASING.md`

**Files:**
- Create: `.github/workflows/migrate.yml`
- Modify: `docs/RELEASING.md`

- [ ] **Step 1:** Create `migrate.yml`: `workflow_dispatch` with optional `db_path`; sync deps; if `db_path` set → `uv run keel migrate --db "$DB"`, else `uv run python scripts/migration_smoke.py`. Comment documents the deferred release-CI seam.
- [ ] **Step 2:** YAML sanity check both workflows.
- [ ] **Step 3:** Update `docs/RELEASING.md`: config-asset section, fresh-deploy seed path (`keel init`), `keel migrate` + the workflow, and PR-body notes note.
- [ ] **Step 4:** Commit `docs(release): config asset, seed/migrate lifecycle, PR-body notes`.

### Task 7: Integration verification

- [ ] **Step 1:** `uv run pytest -q` (full suite green, count up).
- [ ] **Step 2:** `uv run ruff check keel tests packages scripts` clean.
- [ ] **Step 3:** Dry-run notes locally: pipe a small hand-written PR JSON array to `scripts/release_notes.py` and eyeball the grouped, body-inlined output.
- [ ] **Step 4:** `uv run keel migrate` on a temp DB; `uv run keel init-config --live --config /tmp/live.yaml` then `load_config` it.

## Self-Review

- **Spec coverage:** item 4 → Tasks 1, 5; item 5 → Tasks 3, 5; item 6 → Tasks 2, 4, 6. All spec sections mapped.
- **Placeholders:** none — each task names exact files, functions, commands.
- **Type consistency:** `compose_release_notes(prs, categories)`, `clean_pr_body`, `_current_schema_version`, `_template_config_text(live=...)` used consistently across tasks.
