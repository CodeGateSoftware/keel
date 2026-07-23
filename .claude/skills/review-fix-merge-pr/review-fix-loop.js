// Parameterized review<->fix loop for a PR branch that is ALREADY checked out.
//
// args: {
//   prNumber:  number | string   (for labels/reporting only; the diff comes from git)
//   baseBranch: string           (default "main") -- the merge base to diff against
//   intent:    string            (optional) -- what the PR is supposed to accomplish,
//                                  fed to the reviewer as ground truth
//   maxRounds: number            (default 5)
//   testCmd:   string            (optional) -- override the test command the fixer runs
// }
//
// Returns { finalVerdict, finalBlockingCount, rounds, history }.
// Blocking = severity blocker|major. Minor findings are fixed opportunistically; nits are
// reported but never block. The caller (skill) decides whether to merge.

export const meta = {
  name: 'review-fix-loop',
  description: 'Review a checked-out PR branch, fix findings, loop review<->fix until clean',
  phases: [
    { title: 'Review' },
    { title: 'Fix' },
  ],
}

const prNumber = args?.prNumber ?? '(current branch)'
const baseBranch = args?.baseBranch ?? 'main'
const intent = args?.intent ?? '(no explicit intent provided -- infer it from the diff and commit messages)'
const MAX_ROUNDS = Number(args?.maxRounds ?? 5)
const testCmd = args?.testCmd ?? '(discover from pyproject.toml / Makefile -- typically `python -m pytest -q`)'

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['approve', 'request_changes'] },
    summary: { type: 'string', description: 'One-paragraph overall assessment' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
          file: { type: 'string' },
          line: { type: 'integer' },
          summary: { type: 'string' },
          suggested_fix: { type: 'string' },
        },
        required: ['severity', 'file', 'summary', 'suggested_fix'],
      },
    },
  },
  required: ['verdict', 'summary', 'findings'],
}

const FIX_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    changes_made: { type: 'string' },
    tests_passed: { type: 'boolean' },
    test_output_tail: { type: 'string' },
    unaddressed: { type: 'string', description: 'Findings NOT addressed and why; empty if all addressed' },
  },
  required: ['changes_made', 'tests_passed', 'test_output_tail', 'unaddressed'],
}

const BLOCKING = new Set(['blocker', 'major'])

const reviewPrompt = (round, priorContext) => `You are a rigorous code reviewer reviewing PR ${prNumber} (branch is already checked out in the working directory).

Steps:
1. Run: git diff ${baseBranch}...HEAD  -- read the FULL diff.
2. Read the surrounding code for each changed hunk so you review with context, not just the diff. Trace any changed value through to every place it is consumed.
3. Read any added/changed tests and judge whether they actually lock in the intended behavior (would they FAIL under the old code?).

Stated intent of this PR: ${intent}

Review for: correctness, edge cases, missed call sites / surfaces, regression risk to unrelated paths, and test-coverage adequacy. Do NOT invent style nits. This is round ${round} of the review loop.${priorContext}

Report via the structured schema. Set verdict=approve ONLY if there are no blocker/major findings. Every finding needs a concrete, actionable suggested_fix.`

const fixPrompt = (findings) => `You are fixing code review findings on the checked-out PR branch (${prNumber}).

Address these findings (blocker/major are mandatory; also fix minor when the fix is safe and clear):

${findings.map((f, i) => `${i + 1}. [${f.severity}] ${f.file}${f.line ? ':' + f.line : ''} -- ${f.summary}\n   Suggested: ${f.suggested_fix}`).join('\n\n')}

Rules:
- Make the minimal correct change. Do not refactor unrelated code.
- After editing, run the tests for the affected area: ${testCmd}. Capture the result.
- Do NOT commit or push. Just edit files.

Report via the schema: what you changed, whether tests passed, the test output tail, and anything you did NOT address (with reason).`

phase('Review')
let round = 1
let priorContext = ''
let lastReview = null
const history = []

while (round <= MAX_ROUNDS) {
  const review = await agent(reviewPrompt(round, priorContext), {
    label: `review:round-${round}`,
    phase: 'Review',
    schema: REVIEW_SCHEMA,
  })
  lastReview = review
  const blocking = (review?.findings || []).filter((f) => BLOCKING.has(f.severity))
  log(`Round ${round}: verdict=${review?.verdict}, findings=${review?.findings?.length || 0} (${blocking.length} blocking)`)
  history.push({ round, verdict: review?.verdict, findings: review?.findings || [], summary: review?.summary })

  if (review?.verdict === 'approve' && blocking.length === 0) {
    log(`Approved on round ${round}. Loop complete.`)
    break
  }
  if (round === MAX_ROUNDS) {
    log(`Hit maxRounds=${MAX_ROUNDS} with unresolved blocking findings; stopping loop.`)
    break
  }

  phase('Fix')
  const toFix = review.findings.filter((f) => BLOCKING.has(f.severity) || f.severity === 'minor')
  const fix = await agent(fixPrompt(toFix), {
    label: `fix:round-${round}`,
    phase: 'Fix',
    schema: FIX_SCHEMA,
  })
  history[history.length - 1].fix = fix
  log(`Round ${round} fix: tests_passed=${fix?.tests_passed}. ${fix?.changes_made?.slice(0, 120)}`)
  priorContext = `\n\nPRIOR ROUND CONTEXT: in round ${round} a fixer applied: ${fix?.changes_made}. Unaddressed: ${fix?.unaddressed || 'none'}. Re-verify these were done correctly and check for regressions.`
  phase('Review')
  round++
}

return {
  finalVerdict: lastReview?.verdict,
  finalBlockingCount: (lastReview?.findings || []).filter((f) => BLOCKING.has(f.severity)).length,
  rounds: history.length,
  history,
}
