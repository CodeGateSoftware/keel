# Monorepo Foundation — SDD Execution Record

**Status: COMPLETE.** Merged to `main` at `e09263a`; final-review fixes at `c25c696`
and `e7bd9a6`. 732 passing at the time of merge.

Plan: `docs/superpowers/plans/2026-07-18-keel-monorepo-foundation.md`
Spec: `docs/superpowers/specs/2026-07-18-keel-monorepo-architecture-design.md`
Branch: `feat/monorepo-foundation` (merged)

This began as the subagent-driven-development ledger under `.superpowers/sdd/`, which is
gitignored scratch. Moved here so the record survives the machine it was written on. The
per-task briefs, implementer reports, and review diffs it refers to were NOT moved — they
remain local-only scratch and will not be on a fresh clone.

Several items below are explicitly deferred to *later* plans; see the "Deferred to later
plans" section at the end, which is the part still worth acting on.

Pre-flight decisions (human):
- Task 1 golden test is READ-ONLY; regeneration lives in regenerate_golden.py (plan amended).
- Task 2 wildcard re-export shims are DELIBERATE — not a defect, do not relitigate.

## Tasks
- [x] Task 1: complete (commits cd8f91b..7d744ff, review clean, 720/720)
- [x] Task 2: complete (commits 7d744ff..029641b, review clean, 720/720)
- [x] Task 3: complete (commits 029641b..ec9fabf, 1 fix round, review clean, 728/728)
- [x] Task 4: complete (commits ec9fabf..682b052, 1 fix round, review clean, 732/732)

## Minor findings roll-up (for final review triage)
- Task 1 (Minor, reviewer): baseline breadth is narrow — 1 product/granularity,
  s1_filter and min_volume_filter both off. Exercises only the default code path.
  Adequate for proving file-moves are behaviour-preserving; NOT full migration coverage.
- Task 2 (decision): keel-core may depend on python-dotenv as well as pyyaml.
  Plan constraint was wrong; config.py:464 genuinely uses dotenv_values. Plan amended.
- Task 2 (architecture follow-on, NOT this plan): load_secrets lives in keel_core.config,
  but security/secrets.py:111 delegates UP to it — inverted. Spec §4.1 puts secrets in
  keel-security. Fold into the spec-step-4 plan (keel-data/keel-security extraction).
- Task 2 (Minor, reviewer): NON_BINDING_CAP_USD (keel_core/config.py:53) omitted from
  __all__ — the brief's AST script only scanned ClassDef/FunctionDef. Unused externally
  today, but a latent trap for any future `from keel.config import NON_BINDING_CAP_USD`.
- Task 2 (Minor, reviewer): root pyproject still lists pyyaml + python-dotenv directly,
  now redundant (transitive via keel-core). Out of task scope; prune later.
- Task 3 (Minor, reviewer): no test for a caller field named `exc` colliding with a real
  exception payload. Rename logic is generic so likely correct, but unasserted.
- Task 3 (Minor, reviewer): cycle_id collision test never calls bind_cycle, so it only
  proves field_cycle_id is created when there is no real cycle_id to protect. A stronger
  test would bind a real id and assert BOTH keys. Relevant to Task 4's cycle work.
- Task 4 (Minor, reviewer): cb_client uses three per-operation failure events
  (candles/spot/accounts_fetch_failed) where guards/engine use one event + a
  discriminating field. Inconsistent granularity, not wrong.
- Task 4 (Minor, reviewer): `mode=str(mode)` at agent.py:333 is a redundant cast —
  mode is already str. Plan-mandated text.
- Task 4 (Minor, reviewer, NOTABLE): the unbind tests assert current_cycle() is None
  after run_once, but never observe a BOUND state during the cycle. Both tests would
  still pass if bind_cycle(new_cycle_id()) were deleted from run_once entirely —
  silently losing the cross-module correlation this task exists to deliver.

## Final whole-branch review (opus): Fix-before-merge -> all fix-now items closed (c25c696)
Verified boundary by building the keel-core wheel in a clean venv: imports with `keel` absent.
Fixed: cycle_id binding now pinned by a test that fails if bind_cycle is removed; log_exception()
added so 6 live-money sites stop hardcoding telemetry._FIELDS_ATTR; exc_info path tested;
NON_BINDING_CAP_USD in __all__; py.typed shipped. Docs corrected re: baseline coverage (e7bd9a6).

Deferred to later plans (agreed with final reviewer):
- root pyproject pyyaml/python-dotenv now redundant -> clean up in spec step 3
- cb_client per-operation failure event granularity -> normalise in spec step 3 (rewritten there)
- mode=str(mode) redundant cast -> cosmetic
- bind_cycle clobbers rather than restores (no ContextVar token) -> matters once ingest/LLM
  wrap an outer trace; two-line fix then
- `venue` absent from event payloads though spec 10.2 names it stable -> add in step 4 plan
- keel-core has no version specifier -> add when first published
- load_config golden fixture REQUIRED before step 4 moves keel-data
