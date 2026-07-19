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

Deferred to later plans (agreed with final reviewer) — status as of 2026-07-19:

- [x] root pyproject pyyaml/python-dotenv redundant — DONE (2057bf2). Verified by grep that
      nothing under `keel/` imports either before removing; they arrive transitively via
      keel-core. A comment records when to re-add them.
- [ ] cb_client per-operation failure event granularity — DEFERRED ON PURPOSE. Spec step 3
      rewrites that module, so normalising now is throwaway work.
- [x] mode=str(mode) redundant cast — DONE (2057bf2). Confirmed `_confirm_or_bypass` is
      annotated `-> tuple[str, str | None]`, so the cast really was a no-op.
- [x] bind_cycle clobbers rather than restores — DONE (2057bf2). Now returns a ContextVar
      token; `unbind_cycle` resets it. Two tests pin the nesting, both verified to fail
      against the old clobbering behaviour by mutation.
- [ ] `venue` absent from event payloads — STILL OPEN, needs a decision. `guards.py` gained
      `venue=DEFAULT_VENUE` incidentally during the subscription work, but that is one event.
      Doing it properly means deciding WHICH events carry it, which is step-4 plan scope.
- [ ] keel-core version specifier — NOT YET APPLICABLE. Condition is "when first published";
      it is still a workspace dep (`keel-core = { workspace = true }`).
- [x] load_config golden fixture (PREREQUISITE for step 4) — DONE (2f8e09e). Two goldens:
      a fully-specified config and the minimal document whose golden is entirely defaults.
      Read-only, regeneration in `tests/baseline/regenerate_config_golden.py`.

      **Found while mutation-testing that golden:** every config section has TWO defaults —
      the dataclass field default (used by direct construction) and a separate parser default
      in the `raw.get(key, default)` call (the only one `load_config` consults). They can
      drift apart silently: mutating the dataclass default for `unsubscribed_allowance_usd`
      left the ENTIRE baseline suite green. `test_dataclass_defaults_agree_with_the_parser_
      defaults` now compares a defaults-only parse against a default-constructed `Config` and
      closes it for every section at once. They agree today.
