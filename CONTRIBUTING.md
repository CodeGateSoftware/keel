# Contributing to keel

Thank you for considering it. One thing before anything else, because it is the single
biggest surprise contributors hit here: **the documentation bar is unusually high, on
purpose.** Decisions carry their reasoning, rejected alternatives stay recorded, and comments
name what was *measured* rather than what was assumed. A stated bar can be met; an unstated
one can only be resented. The standard is spelled out with a worked example under
[The documentation standard](#the-documentation-standard) — read that section before your
first PR and reviews will feel fair instead of heavy.

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

Python **3.11+** is the floor, and it is measured, not aspirational (#283): the full suite
passes identically on 3.11–3.14, and the one feature binding the floor is 3.11's
`typing.assert_never` (3.10 fails collection on it). The repo itself develops on 3.14
(`.python-version`; `uv python install 3.14`), and CI runs the whole gate on 3.11 too, so the
floor cannot silently rot.
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
  not a no — it is a week.
- **Security reports** follow [`SECURITY.md`](SECURITY.md)'s SLA (acknowledge in 3 days,
  severity and plan in 14), privately, and take precedence over everything here.

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

All six distributions cut from this repo (`keel-trader`, `keel-core`, and the four broker
packages) declare `license = "Apache-2.0"` in their `pyproject.toml`; `tests/test_licensing.py`
fails the build if a seventh distribution appears without it.
