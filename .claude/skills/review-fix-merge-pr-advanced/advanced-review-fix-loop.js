// Advanced multi-lens review<->fix loop for a PR branch that is ALREADY checked out.
//
// Pipeline per round:
//   1. FAN OUT one reviewer per dimension, in parallel. Each reviewer loads a specialized
//      skill (security-audit, performance, code-quality, testing-strategy, ...) and applies
//      that methodology to the PR diff.  (barrier: we need every lens before dedup)
//   2. DEDUP findings across lenses (same file+line+gist collapse to one).
//   3. ADVERSARIAL VERIFY each blocking/minor finding with a skeptic that tries to refute it,
//      so noisy lens output (false positives) never reaches the fixer.
//   4. FIX the confirmed findings with one fixer subagent; it runs tests + linters.
//   5. LOOP back to review with prior-round context until a round yields 0 confirmed
//      blocking findings, or maxRounds is hit.
//
// args: {
//   prNumber, baseBranch="main", intent, maxRounds=4, testCmd,
//   dimensions: [ {key, skill|null, focus} ]   // optional override of the default lens set
// }
// Returns { finalBlockingCount, approved, rounds, history }.

export const meta = {
  name: 'advanced-review-fix-loop',
  description: 'Multi-lens (security/perf/quality/testing) skill-backed PR review, verify, fix, loop',
  phases: [
    { title: 'Review' },
    { title: 'Verify' },
    { title: 'Fix' },
  ],
}

const prNumber = args?.prNumber ?? '(current branch)'
const baseBranch = args?.baseBranch ?? 'main'
const intent = args?.intent ?? '(no explicit intent given -- infer from diff + commit messages)'
const MAX_ROUNDS = Number(args?.maxRounds ?? 4)
const testCmd = args?.testCmd ?? '(discover from pyproject.toml / Makefile -- typically `python -m pytest -q`)'

// Each lens names a skill to load. skill=null means "rigorous general reviewer, no skill".
const DEFAULT_DIMENSIONS = [
  { key: 'correctness', skill: null, focus: 'logic bugs, off-by-one, edge cases, missed call sites, regression risk to unrelated paths, and whether added tests would actually fail under the OLD code' },
  { key: 'security', skill: 'security-audit', focus: 'injection, hardcoded secrets, unsafe deserialization, weak crypto, authz/authn gaps, unsafe subprocess/eval, and tainted-input flow introduced by this diff' },
  { key: 'performance', skill: 'performance', focus: 'hot-path regressions, N+1 / repeated I/O, needless allocations or copies, blocking calls in async code, and algorithmic-complexity changes' },
  { key: 'quality', skill: 'code-quality', focus: 'ruff/mypy violations, missing or wrong type hints, non-Pythonic idioms, dead code, and readability/maintainability of the new code' },
  { key: 'testing', skill: 'testing-strategy', focus: 'coverage of the changed behavior, missing edge/negative/property cases, brittle assertions, and whether the tests pin the intended contract' },
]
const DIMENSIONS = Array.isArray(args?.dimensions) && args.dimensions.length ? args.dimensions : DEFAULT_DIMENSIONS

const REVIEW_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['approve', 'request_changes'] },
    summary: { type: 'string' },
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

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    real: { type: 'boolean', description: 'true only if this is a genuine, in-scope defect worth fixing in THIS PR' },
    severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'nit'] },
    reason: { type: 'string' },
  },
  required: ['real', 'severity', 'reason'],
}

const FIX_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    changes_made: { type: 'string' },
    tests_passed: { type: 'boolean' },
    lint_passed: { type: 'boolean' },
    output_tail: { type: 'string' },
    unaddressed: { type: 'string' },
  },
  required: ['changes_made', 'tests_passed', 'lint_passed', 'output_tail', 'unaddressed'],
}

const BLOCKING = new Set(['blocker', 'major'])
const key = (f) => `${(f.file || '').trim()}:${f.line || 0}:${(f.summary || '').slice(0, 60).toLowerCase()}`

const skillClause = (skill) =>
  skill
    ? `Apply the "${skill}" skill's methodology. Invoke it via the Skill tool if available; otherwise read .claude/skills/${skill}/SKILL.md (or ~/.claude/skills/${skill}/SKILL.md) and follow it. Run the concrete checks/tools that skill prescribes against the changed files where it makes sense.`
    : `Act as a rigorous general correctness reviewer -- no skill needed.`

const reviewPrompt = (dim, round, prior) => `You are the **${dim.key}** reviewer for PR ${prNumber} (branch already checked out).

${skillClause(dim.skill)}

Steps:
1. Run: git diff ${baseBranch}...HEAD  -- read the full diff.
2. Read surrounding code for the changed hunks so you review with context, and trace changed values to every consumer.
3. Review ONLY through the **${dim.key}** lens: ${dim.focus}.

Stated intent of this PR: ${intent}
This is round ${round}.${prior}

Report via the schema. Only report findings that are genuinely in-scope for THIS diff -- do not file pre-existing tech debt in untouched code. Set verdict=approve if you find no blocker/major issues in your lens. Every finding needs a concrete suggested_fix.`

const verifyPrompt = (f) => `Adversarially verify this code-review finding on PR ${prNumber} (branch checked out). Try to REFUTE it. Read the actual code at ${f.file}${f.line ? ':' + f.line : ''} and the diff (git diff ${baseBranch}...HEAD) before deciding.

Finding [${f.severity}] from the ${f.lens} lens: ${f.summary}
Proposed fix: ${f.suggested_fix}

Decide: is this a REAL, in-scope defect that should be fixed in this PR? Default to real=false if it is speculative, pre-existing in untouched code, a pure style preference dressed up as a bug, or not actually reachable. If real, set the severity you believe is correct (you may downgrade/upgrade).`

const fixPrompt = (findings) => `You are fixing VERIFIED review findings on the checked-out PR branch (${prNumber}).

Address these (blocker/major mandatory; fix minor when safe and clear):

${findings.map((f, i) => `${i + 1}. [${f.severity}] (${f.lens}) ${f.file}${f.line ? ':' + f.line : ''} -- ${f.summary}\n   Suggested: ${f.suggested_fix}`).join('\n\n')}

Rules:
- Minimal correct changes only; no unrelated refactors.
- After editing: run tests (${testCmd}) AND the linter (ruff/mypy if configured -- check pyproject.toml). Capture results.
- Do NOT commit or push.

Report via the schema.`

let round = 1
let prior = ''
const history = []
let lastConfirmedBlocking = 0

while (round <= MAX_ROUNDS) {
  phase('Review')
  // 1. Fan out lenses in parallel -- barrier: dedup needs every lens' output.
  const reviews = await parallel(
    DIMENSIONS.map((d) => () =>
      agent(reviewPrompt(d, round, prior), { label: `review:${d.key}:r${round}`, phase: 'Review', schema: REVIEW_SCHEMA })
        .then((r) => ({ dim: d.key, review: r }))
    )
  )
  const raw = reviews
    .filter(Boolean)
    .flatMap(({ dim, review }) => (review?.findings || []).map((f) => ({ ...f, lens: dim })))

  // 2. Dedup across lenses.
  const seen = new Map()
  for (const f of raw) if (!seen.has(key(f))) seen.set(key(f), f)
  const deduped = [...seen.values()]
  const candidates = deduped.filter((f) => BLOCKING.has(f.severity) || f.severity === 'minor')
  log(`Round ${round}: ${raw.length} raw findings -> ${deduped.length} deduped, ${candidates.length} candidates to verify`)

  // 3. Adversarial verify (skip nits).
  phase('Verify')
  const verified = (
    await parallel(
      candidates.map((f) => () =>
        agent(verifyPrompt(f), { label: `verify:${f.lens}:${(f.file || '').split('/').pop()}`, phase: 'Verify', schema: VERDICT_SCHEMA })
          .then((v) => (v?.real ? { ...f, severity: v.severity || f.severity, verify_reason: v.reason } : null))
      )
    )
  ).filter(Boolean)
  const confirmedBlocking = verified.filter((f) => BLOCKING.has(f.severity))
  lastConfirmedBlocking = confirmedBlocking.length
  history.push({ round, raw: raw.length, deduped: deduped.length, confirmed: verified, confirmedBlocking: confirmedBlocking.length })
  log(`Round ${round}: ${verified.length} confirmed (${confirmedBlocking.length} blocking)`)

  if (confirmedBlocking.length === 0) {
    log(`Round ${round}: no confirmed blocking findings. Loop complete.`)
    break
  }
  if (round === MAX_ROUNDS) {
    log(`Hit maxRounds=${MAX_ROUNDS} with ${confirmedBlocking.length} blocking findings unresolved.`)
    break
  }

  // 4. Fix confirmed blocking + minor.
  phase('Fix')
  const toFix = verified.filter((f) => BLOCKING.has(f.severity) || f.severity === 'minor')
  const fix = await agent(fixPrompt(toFix), { label: `fix:r${round}`, phase: 'Fix', schema: FIX_SCHEMA })
  history[history.length - 1].fix = fix
  log(`Round ${round} fix: tests=${fix?.tests_passed} lint=${fix?.lint_passed}. ${fix?.changes_made?.slice(0, 120)}`)
  prior = `\n\nPRIOR ROUND: a fixer applied: ${fix?.changes_made}. Unaddressed: ${fix?.unaddressed || 'none'}. Re-verify these landed correctly and hunt for regressions your lens cares about.`
  round++
}

return {
  approved: lastConfirmedBlocking === 0,
  finalBlockingCount: lastConfirmedBlocking,
  rounds: history.length,
  dimensions: DIMENSIONS.map((d) => d.key),
  history,
}
