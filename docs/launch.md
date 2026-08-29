# The pre-launch gate and the announcement plan

A project that attracts attention before it can absorb it dies of that attention. This
document is the gate (#291): **nothing is announced until every box below is ticked.**
When a box is ticked it gets its evidence link, and the gate itself ratchets — a box can
be ticked, but the gate cannot be quietly removed from the repo that needs it
(`tests/test_launch_gate.py` pins it). The plan below the gate is what happens *after*
the last box, in the order given, and never before.

## The gate

- [x] **Phase 6 fully closed** — licence detected by GitHub (Apache-2.0), description and
  topics set, `SECURITY.md` with private-vulnerability reporting enabled, the positioning
  statement ("not a fatwa engine") in the README's first screen. Milestone 9 closed with
  all four issues done (#296–#298 and the licence work).
- [x] **Phase 7 fully closed** — the README rewritten for the stranger (#300),
  `CONTRIBUTING.md` at the documentation standard (#299), the Contributor Covenant with
  the religious-disagreement stance (#302), issue/PR templates (#303), Discussions live
  with a *Compliance & classification* category, and nine real `good first issue`s
  (#308–#316) plus `help-wanted` on the deeper ones. Milestone 10 closed.
- [x] **The fiqh basis published** — `docs/fiqh-basis.md`: every encoded ruling with its
  in-repo source, attested-vs-computed, the open questions, how to disagree (#317/#288).
- [x] **The scholarly-review stance decided and stated honestly** — "No scholarly review
  of keel's fiqh basis has occurred," the review path defined, the outreach shortlist in
  the document as a plan, not a claim (#318/#289).
- [x] **The Arabic entry point** — `README.ar.md` with the switcher, terminology exact,
  scope stated (#319/#290).
- [x] **CI green on `main`** — the matrix leg (3.14; two legs, 3.11 and 3.14, came in #301 and
      collapsed to one when the floor rose to 3.14), and
  the merge gate (`test` context) has been the required context since #268.
- [x] **The code-quality scans actually configured** — tokenless and always on
  (#320): Dependabot over every manifest, a weekly `pip-audit` over the exported lock,
  CodeQL on Python. `code-quality.yml` remains the optional Sonar/Snyk tier for if those
  tokens are ever created. *Open item to verify after the announcement lull:* the five
  `packages/*` Dependabot entries share the root `uv.lock` — confirm they are live in
  the Dependabot log; if inert, collapse to the root entry.
- [x] **A maintainer response commitment that is honest for one person, stated in
  `CONTRIBUTING.md`** — issues triaged within 3 days, PRs first-reviewed within a week,
  security routed to `SECURITY.md`'s SLA.

## The audience, in order

Small, high-trust communities beat a broad launch: one credible post in the right room
outperforms a Show HN, and a Show HN *first* is the failure mode this ordering exists to
prevent. One venue at a time, and answer every reply in the first 48 hours — attention
that goes unanswered dies unanswered.

1. **Islamic fintech practitioner networks** (IFN and similar): the people whose day job
   is exactly this problem, who will read the screening axes before the trading code.
2. **`r/islamicfinance`, Muslim developer Discord and Telegram groups**: the audience the
   Arabic README exists for; post the honest result in the first paragraph.
3. **Islamic finance programmes — IIUM, INCEIF, Durham**: the same shortlist as the
   scholarly-review outreach; a review may start as a conversation a post begins.
4. **Only then: Hacker News / Reddit / Lobsters** — after the smaller rooms have found
   the repo, so the first wave of questions comes from people who already understand
   what "not a fatwa engine" means.

## What the announcement must say

Lead with the compliance engine, not the trading bot. State the measured result **in the
post itself** — no shipped rule family is net-positive at the taker fee actually paid:
**0 of 90** and **0 of 82** under production-faithful execution
([the experiment record](experiments/2026-08-13-restated-under-a-production-faithful-engine.md)).
Being the one who says it first is the whole credibility play; a post that hides the
result hands it to the first commenter. Say the boundary and the stance plainly too —
**keel is not a fatwa engine**, and **No scholarly review of keel's fiqh basis has
occurred** — and point at what is asked for: the `good first issue`s, the review path,
the Arabic README.

### The draft, ready to adapt

> I built an open-source Shariah-compliance **engine** for spot crypto trading — not a
> trading bot with a halal coat of paint, but the compliance machinery: allowlist
> admission where Shariah classifications are attested with a source and never inferred
> from market data, a fails-closed screen, nineteen un-overridable safety rails including
> §65.4 *qabd* (constructive possession) encoded as an executable check, and an
> audit trail of who attested what.
>
> The honest measured result, stated up front: **no shipped rule family is net-positive
> at the taker fee actually paid — 0 of 90 and 0 of 82 under production-faithful
> execution.** The project's point is the enforcement machinery and the honest
> measurement, not a claim of alpha.
>
> Two things it is not: **keel is not a fatwa engine. It is an enforcement engine for a
> ruling you supply.** So two operators following different schools get different
> answers from the same code, by design. And **no scholarly review of keel's fiqh basis
> has occurred** — the basis is one operator's sourced reading, published as
> docs/fiqh-basis.md precisely so it can be audited and challenged; the review path is
> defined and the review itself is not claimed.
>
> There is an Arabic entry point (README.ar.md), the fiqh basis document with its
> sources, and good-first-issues open. If you want an auditable screening engine and are
> willing to help build one: https://github.com/CodeGateSoftware/keel

## The non-goal

Do not announce to get stars. Announce to find the handful of people who want an
auditable Shariah screening engine and will help build one — a hundred quiet readers who
check the sources beat a thousand who upvote the title.
