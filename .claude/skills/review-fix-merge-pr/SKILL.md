---
name: review-fix-merge-pr
description: Review a GitHub PR with a subagent, fix the findings with another subagent, and loop review<->fix until the branch is clean, then squash-merge. Use when the user wants a PR (by number, or the current branch's PR) automatically reviewed, fixed, and merged.
---

# Review → Fix → Merge a PR

Runs an adversarial review↔fix loop on a PR branch using the `Workflow` tool, then merges
once the loop comes back approved with no blocking findings.

## When to use

The user asks to review-and-merge a PR, "review then fix then merge", or a self-correcting
review loop on a PR. Works with a PR number (`/review-fix-merge-pr 126`) or, with no argument,
the PR for the current branch.

## Steps

1. **Resolve the target PR.**
   - If a PR number was given, use it.
   - Otherwise: `gh pr list --head "$(git branch --show-current)" --state open --json number,title,headRefName,baseRefName`.
   - If none is found, tell the user and stop.

2. **Check out the PR branch** (so the fixer edits the right files):
   `gh pr checkout <PR>`. Note the base branch (usually `main`) and skim the diff /
   commit messages to state the PR's **intent** in one sentence — the reviewer uses it as
   ground truth.

3. **Run the loop workflow.** Invoke `Workflow` with the bundled script and `args`:

   ```
   Workflow({
     scriptPath: "<this skill dir>/review-fix-loop.js",
     args: { prNumber: <PR>, baseBranch: "<base>", intent: "<one-sentence intent>" }
   })
   ```

   Optional args: `maxRounds` (default 5), `testCmd` (override the fixer's test command).
   The workflow spawns one **review** subagent and, per round with blocking findings, one
   **fix** subagent, looping until `verdict=approve` with 0 blocking (`blocker`/`major`)
   findings — or `maxRounds` is hit. It never commits or pushes.

4. **Report the findings** from the returned `history` to the user (verdict, each round's
   findings by severity, what the fixer changed). Do not fabricate — read the result.

5. **Decide on merge:**
   - If `finalVerdict === "approve"` and `finalBlockingCount === 0`:
     - If the fixer changed files, commit them on the branch and push
       (`git commit -am "fix: address review findings" && git push`), then wait for CI.
     - Confirm CI is green (`gh pr checks <PR>`), then squash-merge: `gh pr merge <PR> --squash`.
   - If blocking findings remain after `maxRounds`, **do NOT merge** — surface them and stop.

6. **Confirm** the merged state (`gh pr view <PR> --json state,mergedAt,url`) back to the user,
   and mention any non-blocking `minor`/`nit` findings left as optional follow-ups.

## Notes

- Blocking = `blocker` or `major`. `minor` findings are fixed opportunistically; `nit`s are
  reported but never block a merge.
- The review and fix agents share the working directory and run sequentially (one fixer at a
  time), so no worktree isolation is needed.
- To iterate on the loop logic, edit `review-fix-loop.js` in this skill directory.
