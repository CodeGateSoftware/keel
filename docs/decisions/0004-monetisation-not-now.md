# 0004 — Monetisation is not now, and affiliate links are a permanent no

Date: 2026-08-28 · Issue: #603 · Status: decided

## Context

keel is Apache-2.0, six weeks old (created 2026-07-15), 3 stars, no server, no accounts. Its
headline measured result, stated first by the project itself: **no shipped rule family is
net-positive at the taker fee actually paid on this venue, and the viable parameter/fee
intersection is empty under production-faithful execution** (README.md;
[`docs/experiments/2026-08-13-restated-under-a-production-faithful-engine.md`](../experiments/2026-08-13-restated-under-a-production-faithful-engine.md)).
The pitch is the enforcement machinery — deterministic safety rails, attested asset screening,
*qabd* encoded as a check — and the honesty of the measurement, not a claim of alpha.

#603 asks that keel record its own monetisation answer, prompted by a direct comparison:
[jesse-ai/jesse](https://github.com/jesse-ai/jesse) (MIT, 8.4k stars, created 2018) is the
closest sibling project — same domain (algorithmic crypto trading), same open-core shape — and
its founder published the reasoning in full: *"The story behind Jesse's development"* (Apr
2021) evaluated five monetisation options on the record and chose one. The arc since is public
too: **$1600 list / $800 early-access lifetime (2021) → subscription tiers with lifetime
grandfathered (Jul 2024) → paid roadmap votes allocated by plan tier (Aug 2026)**, plus exchange
**affiliate links** (KuCoin 15% fee discount, Apex 5%, Lighter) added as a second revenue
stream. Jesse's live trading is gated by a `LICENSE_API_TOKEN` validated against their server,
IP-tied, capped at two concurrent sessions — the paid plugin is closed source; the strategy
engine around it stays open.

This record is not a proposal to change anything keel ships. It is the answer to "should keel
monetise", written down once, so each new reader does not re-derive it — in the style ADR 0002
set for a durable decision with a named reopening condition.

## Options

The same five options Jesse's founder evaluated, with keel's own reasoning against keel's own
situation — not a restatement of theirs.

**Donations.** Jesse's founder called donation-based projects "constantly fighting for their
survival" — true, but beside the point here: keel has no running cost donations would offset.
There is no server, no hosted infrastructure, no bill a donation pays down. A donate button
with nothing behind it to fund is a solicitation, not a mechanism. Rejected, not deferred — it
has no object.

**Exchange sponsorship.** Jesse's founder reported being turned down by an exchange specifically
*for being open source* and for lacking a monitoring mechanism the exchange could point to.
keel's position is worse for the opposite reason: it is six weeks old, unaudited by any third
party, and its own headline result is that its shipped strategies lose money after fees. No
exchange sponsors a tool whose public pitch is "this doesn't make money." Deferred, not
rejected — the blocker is adoption and track record, not the mechanism itself.

**Issuing a token.** Jesse's founder's own bar — "projects should only issue tokens if they're
solving a problem only a token can solve" — is one keel fails immediately. keel enforces a
Shariah ruling the operator supplies; nothing about that requires or benefits from a token, and
a compliance tool issuing a speculative instrument is close to self-parody. Rejected outright,
no reopening condition needed.

**SaaS.** A hosted version of keel would mean keel holding operator strategies, credentials, and
trade history on a server it runs — the exact trade Jesse's founder named ("you'd have to give
up your privacy… your strategies will be hosted by a third party"). For keel specifically this
cuts deeper: the entire audit story is that the operator's own database is the record, on the
operator's own machine, readable and diffable without asking anyone. A SaaS layer converts an
auditable local tool into a custodial one and puts keel between the operator and their broker
credentials — a trust posture the project does not want to hold. Rejected on the same grounds
as the plugin below, for a related but distinct reason: this one is about custody, not license.

**A closed-source paid live-trade plugin.** This is Jesse's chosen answer, and it is the worst
fit for keel of the five, not the best. Jesse's plugin sits at the order-placement boundary of a
trading bot generically; keel's order-placement boundary (`keel/execution/executor.py`,
`guards.py`, `sizing.py`) is not incidental machinery — it is the compliance enforcement itself,
the rails that make a screened, attested asset actually get *bought* the way the attestation
says it must, and the part every claim in the README about auditability depends on being
readable. Closing that code to sell it would mean relicensing the one part of keel whose
open-source, plain-SQL, no-ORM inspectability is the entire pitch. A paid plugin bolted onto a
different seam (reporting, alerting, a hosted dashboard) is conceivable without this problem —
but that is not what Jesse built, and it is not what #603 is asking keel to decide on. Rejected
for the live-execution path specifically; see Trigger 2 below for the narrow door left open on
adjacent surfaces.

**Affiliate links** (not one of the five, but named explicitly because Jesse added it as a
second stream and #603 calls it out as the one that looks free). See below — this is not
deferred alongside the others. It is refused.

## Decision

**Not now, on all five mechanisms above — and affiliate links to trading venues are a
permanent no, not a "not yet".**

The issue's own reasoning, which is the reasoning:

**The sequencing argument.** Jesse open-sourced in 2018, released publicly in 2020, and
monetised in 2021 — a paid tier arrived after three years of a public project and an audience
that had already formed around it. keel is six weeks old with 3 stars. There is no audience yet
to sell to, no track record to sponsor, and no adoption to point a token or a subscription at.
Building `/referrals` and `/user/api-tokens` now is building a checkout before there is a shop.
Every one of the four mechanisms above that is "deferred" rather than "rejected" is deferred
specifically on this axis: they might make sense once keel has what Jesse had in 2021, and it
does not have that yet.

**The affiliate-link conflict is not a sequencing problem — it does not get better with time.**
keel's central measured finding, stated in its own README before anything else, is that **fees
are the binding constraint**: the reason no shipped rule family clears is cost, not signal.
Affiliate revenue is paid *per user routed to a venue*, which makes the operator's fee schedule
something keel would have a direct financial stake in — the exact quantity its own research
says decides whether a strategy is viable at all. A KuCoin discount code or an Apex referral
link does not change what the code executes, but it changes what the project is incentivised to
say about fees, and keel's credibility rests entirely on that assessment being disinterested.
This is worse at year five than at week six, not better — a larger audience is a larger flow of
referral revenue riding on the same conflicted assessment. There is no scale at which this
becomes clean. It is excluded from the trigger list below on purpose: no measurement reopens it.

**Paid roadmap voting** needs accounts, plans, and a billing relationship before the first vote
is ever cast — all three are infrastructure keel does not have and, per the sequencing argument,
should not build yet. If keel ever has a roadmap worth contesting, it stays public and free
until there is a paying base large enough for tiered voting to mean anything.

**A closed live-trade plugin is a worse fit for keel than it was for Jesse**, independent of
sequencing. Jesse's plugin closes a feature; a live-trade plugin for keel would close the
compliance rails themselves — `keel/execution/executor.py` and the guard chain around it are
not one feature among several, they are the mechanism the whole project exists to make
auditable. Relicensing that path to sell it does not shrink the open-source surface, it hollows
out the one claim ("almost nobody has this compliance machinery, which is the part worth
reading") that makes keel worth looking at over any other trading bot.

**Standing rules this decision fixes:**

1. No accounts, billing relationship, license-token gate, or server-side validation gets added
   to keel while this record stands. keel stays a tool that runs entirely on the operator's own
   machine, and that includes every deployment profile — live, paper-daily, paper-hourly.
2. **Affiliate or referral links to any trading venue, broker, or exchange are never added** to
   keel, its documentation, or its site — not deferred, refused, and not reopened by any trigger
   in this record. If a future maintainer wants to revisit this specific point, that is a new
   decision record arguing against this one directly, not a trigger firing.
3. Live order placement (`keel/execution/`) stays inside the Apache-2.0 tree in full, always. A
   paid offering, if one is ever built, is built beside that boundary, never across it.
4. The four triggers below are the complete set. A new trigger is a new decision record, not an
   edit to this one.

## The triggers

Framed in users and demonstrated demand, per #603's own ask — not in revenue, and not in
maintainer time, which is a real pressure but not the one this record is answering.

| # | What fires it | The evidence that answers it | Who observes it |
|---|---------------|-------------------------------|-----------------|
| 1 | keel reaches an audience shaped like Jesse's pre-2021 one — public for a comparable stretch, with adoption to show for it | Stars trend, count of independently-run deployments reported unprompted in Discussions/issues (not asked-for), count of external contributors landing merged work | Whoever reopens this question — check against Jesse's own 2018→2021 timeline, not a fixed date |
| 2 | The headline finding reverses — a shipped rule family is measured net-positive at the taker fee actually paid | A new experiment record, held to the same rigour as [`2026-08-13-restated-under-a-production-faithful-engine.md`](../experiments/2026-08-13-restated-under-a-production-faithful-engine.md), superseding it | Whoever ran the experiment — this changes what keel would even be selling, from compliance tooling to a demonstrated edge |
| 3 | Operators repeatedly and unprompted ask for something that costs real money to run on their behalf — hosted infra, a maintained third-party integration, priority support — not a feature request, a request to pay for upkeep | A pattern of such asks in Discussions/issues over time, distinct from a single request; the ask itself is the demand signal, no revenue projection needed first | Whoever is triaging Discussions/issues when the pattern becomes visible |
| 4 | keel's own maintenance load demonstrably exceeds what volunteer, spare-time work can carry — evidenced by external contributors, not by the founder's schedule | Share of merged work landing from non-founder contributors, sustained over a period, alongside a visible backlog that spare-time maintenance cannot clear | Whoever is triaging the backlog when it stops clearing |

None of these four reopens affiliate links (rule 2, above) or SaaS's custodial question — those
are answered independently of adoption scale. Trigger 1 and Trigger 4 can both point toward
donations or sponsorship being worth a second look sooner than a paid tier would be, since
neither carries the fee conflict a paid feature or a referral link does.

## Consequences

- **The question stops being re-asked.** "Should keel monetise" has a dated answer: not now, for
  four of five options on adoption grounds, and never for affiliate links regardless of
  adoption. A future contributor proposing a referral program or a paid live-trade tier is
  pointed here first.
- **No accounts, billing, or license-gating infrastructure is added on spec.** If Trigger 1 or
  Trigger 3 fires, the infrastructure gets built against demonstrated demand, not ahead of it.
- **The fee-conflict argument is now load-bearing beyond this record.** Any future revenue
  proposal that scales with routing operators toward a particular venue, fee schedule, or
  broker — not just an affiliate link by name — should be read against the same objection:
  keel's credibility depends on its fee assessment staying disinterested, and that is a
  structural property, not a per-mechanism one.
- **If a trigger fires,** the answer is a new decision record superseding this one, sized to
  what actually changed — a green light on sponsorship is not a green light on a plugin, and
  neither is a green light on affiliate links.
