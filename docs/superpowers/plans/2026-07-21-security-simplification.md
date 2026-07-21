# Security simplification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]` checkboxes.

**Goal:** Delete the vault and the passphrase gate; make autonomy a live-read profile choice.

**Architecture:** A new single-row `profile` table (schema 6→7) holds `autonomous`, read live on every order decision. `auto_trade.mode` collapses to `paper|confirm`. The four halt-releasing commands swap the passphrase for an interactive typed confirmation. Rails are untouched throughout.

**Tech Stack:** Python 3.12, click, sqlite3, pytest.

## Global Constraints

- **The 17 rails do not change.** This changes *who is asked*, never *what is allowed*.
- Every safety invariant in the spec's "Core safety invariants" gets a test.
- Autonomy fails closed (absent row ⇒ `False`); `autonomy on` needs a TTY; `autonomy off` never does.
- Autonomy **never** clears a safety halt.
- Full suite + `uv run ruff check keel tests packages scripts` green before each commit.

---

### Task 1: `profile` table + Repository API (schema 6→7)

**Files:** `keel/data/db.py`, `keel/data/repository.py`, `keel/types.py`, `tests/data/test_repository.py`

**Produces:** `Profile(autonomous: bool, updated_ts: int)`; `Repository.get_profile() -> Profile`; `Repository.set_autonomous(value: bool, now_ts: int) -> None`; `db.SCHEMA_VERSION == 7`.

- [ ] Failing tests: absent row ⇒ `autonomous False`; set/get round-trips; `set_autonomous` upserts (never a 2nd row); a v6 DB migrates to v7 and gains the table; `SCHEMA_VERSION == 7` literal tripwire.
- [ ] Run → FAIL. Implement table + `_migrate_v7_profile` + repo methods + `Profile`. Run → PASS. ruff. Commit.

### Task 2: Delete the vault and the passphrase gate

**Files:** delete `keel/security/`, `tests/security/`; `pyproject.toml`; `keel/cli.py`

**Produces:** `_require_interactive_confirmation(action: str, detail: str) -> None` in `cli.py` (typed `yes`, fails closed off-TTY).

- [ ] Failing tests: each of `resume`/`resume-entries`/`record-flow`/`reset-hwm` proceeds on typed `yes`, aborts on anything else, aborts with no TTY.
- [ ] Run → FAIL. Delete both modules + their tests + the `cryptography` dep; remove `_require_authz`, `--passphrase`, `--authz-path`, `DEFAULT_AUTHZ_PATH`; add `_require_interactive_confirmation` and wire the four commands. Run → PASS. ruff. Commit.

### Task 3: Autonomy in the agent and executor

**Files:** `keel/agent.py`, `keel/execution/executor.py`, `tests/test_agent.py`, `tests/execution/test_executor.py`

**Consumes:** `Repository.get_profile()`.
**Produces:** `agent._effective_mode(config, repo) -> Literal["confirm","autonomous"]`; executor `mode: Literal["confirm","autonomous"]`.

- [ ] Failing tests: `autonomous=True` places with no `confirm_fn`; `autonomous=False` still fails closed without one; **a rail veto never reaches placement in autonomous mode**; profile re-read per cycle (flip between two `run_once` calls); `mode: paper` places nothing even when autonomous; kill-switch short-circuits first.
- [ ] Run → FAIL. Replace `_confirm_or_bypass` with `_effective_mode`; retire `arm_bypass`/`is_bypass_armed`/`disarm_bypass` and `bypass_refused_reason`; rename the executor literal. Run → PASS. ruff. Commit.

### Task 4: `keel autonomy on|off|show`

**Files:** `keel/cli.py`, `tests/test_cli.py`

- [ ] Failing tests: `show` prints off by default; `on` with typed `yes` enables and persists; `on` aborts off-TTY and on a non-`yes` answer; `off` disables **without** a TTY; **`on` does not let the four halt commands skip their confirmation**.
- [ ] Run → FAIL. Add the `autonomy` group; remove `arm-bypass`/`disarm-bypass`/`--bypass`. Run → PASS. ruff. Commit.

### Task 5: Config — validate `mode`, drop `bypass_arm_ttl_sec`

**Files:** `packages/keel-core/keel_core/config.py`, `config.yaml`, `keel/templates/config.yaml`, `keel/templates/config.live.yaml`, `tests/fixtures/config_golden_*`, `tests/test_config.py`

- [ ] Failing tests: `mode: bypass` raises `ConfigError` naming the key; `paper`/`confirm` load; `bypass_arm_ttl_sec` is gone from the parsed config.
- [ ] Run → FAIL. Validate mode, drop the field, update all three configs (**root and dev template must stay byte-identical**) and the golden fixtures. Run → PASS. ruff. Commit.

### Task 6: Docs — go-live runbook + spec §14

**Files:** `docs/go-live-runbook.md`, the main design spec's §14

- [ ] Write the runbook against the real flow: `.env` → `keel migrate`/`init` → promote a rule → `keel agent` (confirm) → one tiny supervised order → optional `keel autonomy on`. Amend §14 to record the removal and why. Commit.

### Task 7: Verification

- [ ] `uv run pytest -q` green; `uv run ruff check keel tests packages scripts` clean.
- [ ] Grep-prove the surface is gone: no `authz`, `save_vault`, `arm_bypass`, `bypass` outside history/docs.
- [ ] Smoke: `keel migrate` on a v6 DB; `keel autonomy show/on/off`; `keel autonomy on` piped (must refuse).

## Self-Review

- **Spec coverage:** §3.1→T2, §3.2→T2, §3.3→T1+T3+T5, §3.4→T4, §3.5→T6. Invariants 1,3,6→T3; 2→T4; 4→T4; 5→T3.
- **Placeholders:** none. **Type consistency:** `Profile`, `get_profile`, `set_autonomous`, `_effective_mode`, `_require_interactive_confirmation` used identically across tasks.
