# Subscription Record & Attestation — SDD Execution Record

**Status: COMPLETE.** Merged to `main` at `c7b388a`; test-strength follow-ups at
`0bcfca0` and `70040c7`. 853 passing, ruff and mypy clean, baseline fixture
byte-unchanged throughout.

Plan: `docs/superpowers/plans/2026-07-19-keel-subscription-record-and-attestation.md`
Spec: `docs/superpowers/specs/` (commit 215f396)
Branch: `feat/subscription-record-attestation` (merged and deleted)
Base commit: `a34ae9a`

This began as the subagent-driven-development ledger at `.superpowers/sdd/progress.md`,
which is gitignored scratch. Moved here so the record survives the machine it was written
on. The per-task briefs, implementer reports, and review diffs it refers to were NOT moved
— they remain local-only scratch under `.superpowers/sdd/` (including the previous plan's
under `archive-2026-07-18-monorepo-foundation/`) and will not be on a fresh clone.

Pre-flight decisions (human, both APPROVED as deviations from the plan's text):
- Task 1 `test_the_record_is_frozen` asserts `dataclasses.FrozenInstanceError`,
  NOT the plan's `pytest.raises(Exception)`.
- Task 6 CLI drops the unreachable `return` after each `ctx.exit(1)` (3 sites).
These are deliberate — reviewers must not relitigate them.

Pre-flight verified facts (do not re-derive):
- `config.tiers` defaults (`_default_tiers`, keel_core/config.py:166) are Basic/500/4.99,
  Preferred/10000/29.99, Premium/None/299.99. `_parse_tiers` falls back to these when the
  YAML has no `tiers:` block — which VALID_CONFIG_YAML does not have. Task 6's assertions
  therefore hold, but the plan's prose ("VALID_CONFIG_YAML ... carries Basic/Preferred/Premium")
  is wrong about the mechanism. Values are identical; behaviour is correct.
- `db.connect()` does NOT call `migrate()`, so Task 2's `_v1_database()` fixture is sound.
- Task 2's stated "Expected: 10 passed (9 test functions)" is off by one: the file defines
  10 functions, one parametrized twice = 11 tests.

## Tasks
- [x] Task 1: complete (commits a34ae9a..42f96dd, review clean, 808/808)
- [x] Task 2: complete (commits 42f96dd..38615a5, review clean, 819/819)
- [x] Task 3: complete (commits 38615a5..5934fba, review clean, 827/827)
- [x] Task 4: complete (commits 5934fba..f4711c0, review clean, 832/832)
      NOTE: also renamed keel/cli.py:180 (`_ensure_subscription_seeded`), outside the
      brief's file list but a genuine role-(a) config-field read. Reviewer verified.
      Task 6 deletes that function entirely.
- [x] Task 5: complete (commits f4711c0..e65a5f5, review clean, 843/843)
      13 pre-existing tests modified (guards/executor/account) + 1 fixture in
      tests/test_agent.py (outside brief's list, justified). Opus reviewer verified all 13
      are same-number substitutions with assertions byte-identical; the test_account.py
      one is strictly MORE discriminating after the change.
- [x] Task 6: complete (commits e65a5f5..cd34bed, 1 fix round, review clean, 846/846)
      Opus review found 1 Important: `subscription set`'s `except InvalidOperation` branch
      was live user-facing code with ZERO tests — the deleted
      test_subscription_set_rejects_a_non_numeric_allowance covered SURVIVING behaviour
      under a renamed flag (--monthly-allowance -> --free-volume-usd), not deleted
      behaviour. Implementer's "exercised indirectly" justification was disproven
      (numeric input takes the try body, never the except). Fixed + 4 Minors.
      9 pre-existing tests deleted total; opus verified the other 8 were justified.

## Final whole-branch review (opus) -> fix wave e23d1fc -> VERIFIED, merged to main
Verification verdict: Ready to merge = YES, no Critical/Important. 852 passing.
The `pacing_note` extension the fix implementer added unprompted was checked for
circularity: its stated rationale ("needed for the test") was WRONG — `assert not
result.ok` already discriminated — but the change is independently correct, because
fix 4 newly made it reachable for the veto message to claim a cap of 220 while
rejecting a 150 order. Every existing assertion stays byte-identical.

### Follow-ups
- [x] DONE (0bcfca0): `test_set_rejects_a_nan_allowance` no longer passes against the
  pre-fix broken code. Verified by running it against cd34bed in a scratch worktree
  (fails there with InvalidOperation) and at HEAD (passes).
  CAUTION for future work here: the final reviewer claimed it had confirmed `nan` exits
  with `r.exception is None`. That is NOT observable through click's CliRunner, which
  records SystemExit(1) in result.exception even on a clean ctx.exit(1) — asserting
  `is None` fails at HEAD against correct code. The discriminating assertion is the
  exception TYPE: `assert not isinstance(result.exception, InvalidOperation)`.
  The reviewer's conclusion was right but its stated evidence did not hold.
- [x] DONE (70040c7): added the missing negative control for unattested even_daily
  pacing. Verified by MUTATION rather than by the pre-fix worktree — the pre-fix code
  under-paces, which the positive test already catches, so the control had to be proven
  against the opposite mutation. Hardcoding `pacing = "even_daily"` in the unattested
  branch fails the new control while the positive test still passes: the pair is now
  two-directional, and neither test alone catches both failure modes.
- Deferred cluster, unchanged: `_attest` triplicated across 3 test files with subtly
  different signatures; duplicate-key YAML in test_unsubscribed_allowance_parses;
  `subscription_usd_month` is write-only today.

Verdict was "with fixes". Reviewer traced every path and found NO route by which an
unattested/suspect/lapsed/overdue record yields spending authority. All 8 Done criteria
verified. The one merge-gating find, missed by all six per-task reviews:

  `Decimal('Infinity')` passes every non-negative check. So `unsubscribed_allowance_usd:
  .inf` in YAML, or `subscription set --free-volume-usd inf`, produced an UNBOUNDED live
  spend cap — turning the fail-closed fallback itself into unlimited spend, the exact
  inversion this branch exists to prevent. Rail 14's only test is `projected > cap`.
  Note "unlimited" already has a correct representation: `free_volume_usd is None`.
  Related latent crash: `Decimal("nan")` parses fine, so it escaped the try/except and
  raised uncaught from the `< 0` comparison.

Human approved all 3 plan-conflicting fixes (venue-scoped backfill guard, pacing
asymmetry, degraded-reason ordering). Fix wave also added the missing ceiling assertion
on the one knob that deliberately opens the fail-closed gate.

Reviewer's note ON THE PLAN itself (for the next plan, not this one): when a plan dictates
test bodies verbatim, it should dictate the NEGATIVE assertion alongside every positive
one — especially for a knob that opens a fail-closed gate. Two of this plan's weakest
artifacts came from that omission.

## Minor findings roll-up (for final review triage)
- Task 1 (Minor, reviewer): a parametrize call in tests/test_subscription_record.py is
  split across 3 lines where it fits on one. Purely cosmetic; ruff would not flag it.
- Task 2 (Minor, reviewer): keel/data/db.py module docstring still says "the eight §6
  tables" and lists nine; this change adds a tenth without updating it. Pre-existing
  staleness, now slightly worse.
- Task 2 (Minor, reviewer, FORWARD-COMPAT): the backfill's re-run guard is
  `SELECT 1 FROM broker_subscriptions LIMIT 1` — table-wide, not scoped to
  `venue='coinbase'`. Harmless with one venue; a future second venue's backfill would be
  skipped once ANY row exists. Plan-mandated.
- Task 2 (Minor, reviewer): two brief-mandated defensive fallbacks are never exercised by
  any test — `stored.get("pacing") or "opportunistic"` and
  `stored.get("updated_at") or int(time.time())`. Coverage gap inherited from the brief.
- Task 3: no findings at any severity.
- Task 4 (Minor, reviewer): tests/test_config.py `test_unsubscribed_allowance_parses`
  builds YAML with a DUPLICATE `unsubscribed_allowance_usd` key (0 from the fixture, then
  25 appended after `pacing:`). PyYAML safe_load takes the last, so it passes, but the
  fixture is a duplicate-key document — fragile if the loader changes. Plan-mandated.
- Task 5 (Minor, opus reviewer, COVERAGE — worth fixing at final review): four of the ten
  new rail-14 tests survive deletion of the entire rail-14 block (they assert only the
  ABSENCE of violation keys): allows_a_buy_inside_an_attested_allowance,
  passes_unconditionally_for_an_unlimited_tier, honours_a_raised_unsubscribed_allowance,
  does_not_gate_sells. Brief-mandated verbatim.
- Task 5 (Minor, opus reviewer, COVERAGE): no rail-level test pins the unlimited-BUT-
  degraded path (free_volume_usd=None + SUSPECT) — the one that would be a real-money
  hole. Covered only upstream in test_subscription_record.py. Parametrizing
  test_rail14_fails_closed_on_a_degraded_subscription with None would pin the composition.
- Task 5 (Minor, opus reviewer, COVERAGE): test_rail14_honours_a_raised_unsubscribed_
  allowance never asserts the raised value is a CEILING (no test that 250 > 200 is vetoed).
  Verifies the config value is read, not that it is enforced.
- Task 5 (Minor, opus reviewer): keel/cli.py subscription group help still claims the row
  it writes is authoritative and read fresh by guards.check. False after Task 5. Task 6
  rewrites this group, so it should self-resolve — VERIFY at final review.
- Task 5 (Minor, opus reviewer): `_attest` duplicated near-verbatim in 3 test files;
  used at test_guards.py:471 but defined at :621; two calling conventions for the same
  function coexist in test_guards.py (bare `check(...)` vs `guards.check(...)`).
- Task 5 (Minor, opus reviewer): the report's claim that the even_daily parity test
  discriminates pacing is overstated — the paced boundary (606) sits above the binding
  cash boundary (150), so the grid never probes it. Pre-existing test weakness.
