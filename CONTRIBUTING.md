# Contributing to keel

Thank you for considering it. One thing before anything else, because it is the single
biggest surprise contributors hit here: **the documentation bar is unusually high, on
purpose.** Decisions carry their reasoning, rejected alternatives stay recorded, and comments
name what was *measured* rather than what was assumed. A stated bar can be met; an unstated
one can only be resented. The standard is spelled out with a worked example under
[The documentation standard](#the-documentation-standard) — read that section before your
first PR and reviews will feel fair instead of heavy.

## The contribution workflow, step by step

To maintain code quality and keep the deployment pipeline safe, every contribution — from a
typo fix to a new broker adapter — follows the same path.

### 1. Find or create an issue

- Browse the **Issues** tab of the main repository.
- Comment on the issue you want to work on, so others know it is taken.
- Found a bug or want a feature that has no issue yet? **Open the issue first** and discuss
  the approach before writing code. The [scope section](#scope-what-is-welcome-what-needs-discussion-first-what-is-out)
  says which kinds of changes need that agreement *before* a PR exists.

### 2. Fork the repository

Use the **Fork** button at the top right of the main repository page. This creates a copy of
the project under your personal GitHub account.

### 3. Clone and set up locally

Clone your fork and add the original repository as the `upstream` remote:

```bash
# Clone your fork (replace USERNAME with your GitHub username)
git clone https://github.com/USERNAME/keel.git
cd keel

# Add the original repository as the 'upstream' remote
git remote add upstream https://github.com/CodeGateSoftware/keel.git
```

Then run the development setup and the four gates exactly as written under
[Development setup and the gates a PR must pass](#development-setup-and-the-gates-a-pr-must-pass)
below — `uv sync --all-extras --dev`, then `ruff`, `mypy`, and the full `pytest` suite.

### 4. Create a feature branch

Never make changes directly on `main`. Pull the latest upstream state and branch from it:

```bash
git checkout main
git pull upstream main

# Create and switch to your new feature branch
git checkout -b feature/your-feature-name
# Or, for bug fixes:
git checkout -b fix/your-bug-name
```

### 5. Commit and test your changes

- Keep your commits atomic, focused, and small.
- Write clear, descriptive commit messages in the present tense, with the Conventional
  Commits prefix the releases are cut from (`fix(strategy): fill entries at the next bar's
  open`) — see [Commit convention](#commit-convention).
- Changes here land test-first ([Tests come first](#tests-come-first)), and **all four gates
  must pass locally** before you ask for review. CI runs exactly those commands, so anything
  red locally is red everywhere.

### 6. Push and submit a pull request

Push your feature branch to your fork:

```bash
git push origin feature/your-feature-name
```

GitHub will then show a **Compare & pull request** banner on your fork. Use it, and in the
PR description explain your changes and link the issue it resolves with `Closes #42` (the
issue then closes automatically on merge).

### Code review & security guidelines

- **No direct write access.** All contributions land through a reviewed pull request;
  direct pushes to `main` are blocked by repository branch protection.
- **Review process.** Expect questions, suggested edits, or requests for code adjustments
  before a merge — that is the gates working, not pushback on you. First review arrives
  within the window stated under [What to expect from a solo maintainer](#what-to-expect-from-a-solo-maintainer).
- **Keep syncing.** If your PR waits for review, keep it updated with
  `git pull upstream main` to avoid merge conflicts.

## Governance: rulings vs. machinery

**keel is not a fatwa engine. It is an enforcement engine for a ruling you supply.**

keel never derives a Shariah classification from market data. Every classification is a human
input — recorded with a source and an attributed name (`keel assets attest --source ...
--attested-by ...`) — and keel's job is to enforce what was recorded, deterministically,
rejecting anything unattested. That is what lets one codebase serve operators of different
schools and jurisdictions: the ruling lives in the attestation, not in the code.

For pull requests, that splits contributions into two kinds with different bars:

- **A PR that changes a *default classification*** — what sector a well-known token falls into,
  whether a wrapper counts as `spot`, which backings are admissible — is a ruling arriving in
  code's clothing. It would apply one contributor's fiqh to every operator who upgrades. Such a
  PR must cite a **source** (a scholar, a council, a standard) and is **discussed** before it
  merges; a classification with no source behind it is not mergeable, however confident the
  author.
- **A PR that changes the *mechanism*** — how attestations are recorded, checked, or audited;
  how the screen or the rails run; anything where the ruling stays in the operator's data — is
  **ordinary engineering** and needs only ordinary review.

**If you disagree with a classification, do not litigate it here.** The project does not
adjudicate fiqh and will not become a court with a merge button. Attest your own ruling
locally — `keel assets attest` writes to *your* database, with your source and your name on
it — and run the enforcement engine under it. The disagreement then costs nobody anything:
upstream stays neutral, your deployment follows your ruling, and the audit trail records
exactly who said what. The Shariah reasoning the codebase encodes, ruling by ruling with its
source, is written up in [`docs/fiqh-basis.md`](docs/fiqh-basis.md).

## Development setup and the gates a PR must pass

Python **3.14+** is the floor, and it is a policy floor, not a measured one — the distinction
matters if you are about to argue with it. The suite passes identically on 3.11–3.14; nothing
here needs 3.14. The floor was 3.11 for exactly that reason (#283), and went up because keel now
ships a signed desktop bundle and an installer that bootstrap their own interpreter, so an end
user's system Python is no longer what keel runs on. The floor now reaches contributors and
packagers rather than everyone who installs.

It also buys two things that were blocked at 3.11: `ruff` can target `py314` (under the old
floor `ruff format` could emit PEP 758 syntax that 3.11–3.13 cannot parse), and the `numpy.*`
mypy override is gone. `uv python install 3.14` if you do not have it; `.python-version` pins
the exact patch. CI runs the whole gate on 3.14, so the floor cannot silently rot.

If the floor blocks you, say so in an issue — "a contributor was actually blocked" is the
argument that would lower it again, and there is no feature standing in the way.
Then:

```bash
uv sync --all-extras --dev   # everything: the workspace, dev deps, the conformance extra
uv run ruff check            # lint — must pass clean
uv run mypy                  # types — must pass clean
uv run pytest -q             # the full suite — must pass (CI runs exactly this)
```

All four must be green before you ask for review. CI runs the same commands, so anything red
locally is red everywhere. The suite is fast (tens of seconds) — run it freely.

## The documentation standard

A comment or docstring here is acceptable when it does three things: **it says why** (the
constraint the code cannot show), **it names what was measured** (numbers, incidents, the
specific input that broke — not "this is faster"), and **it says what it would take to change
the decision** (which assumption, overturned, reverses it). Code reviews enforce this; the
point of writing it down is that you can enforce it on yourself first.

A worked example, from `_open_exposure_by_asset` in `keel/execution/guards.py` — the net
at-risk figure that the exposure and concentration caps read:

> ⚠️ **An unparseable `product_id` is handled by SIDE, and always logged at WARNING.** A
> malformed **BUY** is COUNTED, under whatever key `_asset` gives it; a malformed **SELL** is
> SKIPPED. Both choices are the same choice — never let a row nobody can read make this
> figure SMALLER — and it is the sign of the row, not the fact of the row, that decides which
> action achieves that.

What makes this acceptable:

- **It says why, at the level the code cannot.** The code shows a branch on side; the comment
  shows the *invariant* the branch serves — the figure feeds caps, and a smaller figure is a
  looser cap, so unreadable rows may only ever err towards refusing an order.
- **It names what was measured.** Not "SELLs are risky" but the arithmetic: a counted
  `$800` unreadable SELL against a `$900` BUY measures `$100`, and past the BUY total the
  `if amt > 0` filter *deletes the bucket entirely*. Someone checked; the comment shows the
  check.
- **It records the decision's history and its reversal conditions.** It supersedes an earlier
  unconditional rule and its unconditional opposite, names which evidence would flip it again
  ("neither survives a SELL"), and stays honest about the impossible case it still guards.

You do not need to write a essay per function. You need those three properties wherever a
choice was made that a reader could plausibly make differently.

## Tests come first

Changes land test-first: write the failing test, run it, watch it fail **for the right
reason** — the assertion you meant to assert, not an import error or a typo — then make it
pass. Your PR should carry that evidence: a test whose failure message is the bug being
described, in the PR description or the diff's story. A test that has never been seen red is
a test that may be green for no reason.

The suite's own docstrings follow the documentation standard too; `tests/test_packaging.py`
is a good read for how a test argues its own existence.

## Commit convention

[Conventional Commits](https://www.conventionalcommits.org/), matching the existing history:
`fix(strategy): fill entries at the next bar's open`, `docs(experiments): ...`,
`chore(release): ...`. The prefix is load-bearing — releases and changelogs are cut from it
(see `docs/RELEASING.md`) — so an untyped commit is not a style nit, it is a missing record.

## Scope: what is welcome, what needs discussion first, what is out

**Welcome, PRs directly:**

- Bug fixes with a failing test that reproduces them.
- Documentation for something that deserves it and lacks it (a rail, a command, a failure
  mode) — written to the standard above.
- Tooling, packaging, and test-quality improvements.
- New broker adapters behind the port (`packages/keel-broker-*`), discussed in an issue first
  only if they need port changes.

**Needs an issue and agreement BEFORE the PR:**

- Anything touching a **rail** (`keel/execution/guards.py`): a rail's semantics are the
  product, and a subtle change distributes itself to every operator who upgrades.
- Anything changing a **default classification** — see the governance section above for the
  source-and-discussion bar those carry.
- New dependencies, and anything that widens the public surface of `keel-broker-api` (every
  adapter codes against it).

**Out of scope:**

- Shariah rulings as code defaults — attested locally, never merged (see governance).
- "Make the bot profitable" — the honest measured state of the rules is recorded in
  `docs/experiments/`, and that record is the project's posture, not a to-do list.
- Anything that weakens a fails-closed path to make an operational annoyance go away; the
  annoyance is the smaller problem.

## What to expect from a solo maintainer

This is a solo-maintained project, and the response times below are what one person can
actually keep — stated here (#291) so a contributor's expectations come from the repo,
not from the 24/7 responsiveness a large project can imply:

- **Issues** are triaged within **3 days** (a label and a first reply; a fix may take
  longer, and the triage will say so).
- **PRs** get a first review within **a week** when the gates pass. A slower review is
  not a no.
- **Security reports** follow [`SECURITY.md`](SECURITY.md)'s SLA, privately, and take
  precedence over everything here.

## Licence: why Apache-2.0

keel is licensed under [Apache-2.0](LICENSE). That was a decision, not a default, and the
reasoning is recorded here so it can be challenged in place rather than dug out of a merged
pull request.

keel moves real money on a public exchange, so two properties of a licence matter more here
than they would for a typical library:

- **The warranty disclaimer.** Apache-2.0 disclaims warranties and liability in explicit,
  business-reviewed language. Software that places live orders needs that sentence to be as
  strong as it can be.
- **The patent grant.** Contributors and users get an explicit grant. MIT offers none.

The alternatives, and why they lost:

| option | why it lost |
| --- | --- |
| **AGPL-3.0** | Prevents a closed hosted fork, but deters exactly the contributors this project wants — many employers forbid AGPL code on work machines, and keel's audience includes people reading the source at work. |
| **MIT** | Shortest and most familiar, but no patent grant and a thinner warranty disclaimer — both matter more than brevity here. |

Apache-2.0 permits a closed hosted fork; we accept that. The compliance engine's value is the
audit trail it produces, which a hosted fork cannot hide.

All eight distributions cut from this repo (`keel-trader`, `keel-core`, and the six broker
packages) declare `license = "Apache-2.0"` in their `pyproject.toml`; `tests/test_licensing.py`
fails the build if a ninth distribution appears without it.
