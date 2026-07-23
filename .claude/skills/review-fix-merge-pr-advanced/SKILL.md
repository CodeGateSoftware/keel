---
name: review-fix-merge-pr-advanced
description: Multi-lens PR review that fans out parallel subagents each backed by a specialized skill (security-audit, performance, code-quality, testing-strategy, correctness), adversarially verifies findings, fixes them, loops until clean, then squash-merges. Use when the user wants a thorough, multi-dimension review-and-merge of a PR — security + performance + best practices + tests — not just a single reviewer.
---

# Advanced Multi-Lens Review → Verify → Fix → Merge a PR

The thorough sibling of `review-fix-merge-pr`. Instead of one reviewer, it runs **one reviewer
per dimension in parallel**, each loading a specialized skill, then **adversarially verifies**
every finding before a fixer touches code — so noisy lens output never causes churn. Loops
review→verify→fix until a round yields zero confirmed blocking findings, then merges.

## When to use

The user wants a deep, multi-dimension review — "review it for security and performance and
best practices then fix and merge", "thorough review", "audit this PR across the board". For a
quick single-reviewer pass, use `review-fix-merge-pr` instead.

## The lenses (default)

Each maps to a repo skill; the reviewer subagent loads it (via the Skill tool, or by reading
`.claude/skills/<skill>/SKILL.md`) and applies its methodology to the diff:

| Lens         | Skill              | Looks for |
|--------------|--------------------|-----------|
| correctness  | *(general)*        | logic bugs, edge cases, missed call sites, regressions, test rigor |
| security     | `security-audit`   | injection, secrets, unsafe deserialization, crypto, authz, tainted input |
| performance  | `performance`      | hot-path regressions, N+1/repeated I/O, allocations, blocking async, complexity |
| quality      | `code-quality`     | ruff/mypy issues, typing, Pythonic idioms, readability |
| testing      | `testing-strategy` | coverage of the change, edge/negative cases, brittle assertions |

Override via the `dimensions` arg — e.g. add `{key:'api', skill:'api-design', focus:'...'}` or
drop lenses that don't apply to the PR.

## Steps

1. **Resolve the PR** (argument, or the current branch's open PR via
   `gh pr list --head "$(git branch --show-current)" --state open --json number,baseRefName`).
   Stop and tell the user if none.
2. **Check out** the PR branch (`gh pr checkout <PR>`), note the base branch, and write a
   one-sentence **intent** from the diff/commits.
3. **Pick the lenses.** Default set above; trim lenses that are irrelevant to the change (e.g.
   skip `performance` on a docs-only PR) and add `api-design`/`library-review` if apt.
4. **Run the workflow:**
   ```
   Workflow({
     scriptPath: "<this skill dir>/advanced-review-fix-loop.js",
     args: { prNumber: <PR>, baseBranch: "<base>", intent: "<one sentence>",
             maxRounds: 4 /*, dimensions: [...], testCmd: "..." */ }
   })
   ```
   It fans out the lenses in parallel each round, dedups, verifies each candidate finding with
   a refute-first skeptic, fixes the confirmed ones (running tests + linter), and loops. It
   never commits or pushes.
5. **Report** from the returned `history`: per-round raw→deduped→confirmed counts, the confirmed
   findings by lens/severity, and what the fixer changed. Read the result — never fabricate.
6. **Merge decision:**
   - If `approved === true` (0 confirmed blocking): if the fixer changed files, commit + push,
     wait for green CI (`gh pr checks <PR>`), then `gh pr merge <PR> --squash`.
   - If `finalBlockingCount > 0` after `maxRounds`: **do not merge** — surface the blockers.
7. **Confirm** merged state and list any leftover `minor`/`nit` findings as optional follow-ups.

## Notes

- Blocking = `blocker`|`major` (post-verification). `minor` is fixed opportunistically; `nit`
  is reported, never blocks, never verified.
- The parallel fan-out is a genuine barrier: dedup needs every lens before verify/fix.
- Reviewers/verifiers are read-only in spirit; only the single fixer per round edits files, so
  no worktree isolation is needed. Concurrency is capped by the Workflow runtime.
- More tokens than `review-fix-merge-pr` (N reviewers + M verifiers per round). Use it when
  thoroughness matters; use the basic skill for quick merges.
