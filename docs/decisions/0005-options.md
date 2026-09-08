# 0005 — Options: ⟨VERDICT UNSIGNED⟩

Date: 2026-09-08 · Issue: #637 · Status: **DRAFT — the Decision section is unsigned**

> **This record does not decide anything yet.** Everything below the Options section is written so
> the maintainer can strike one line and sign the other. The jurisprudential question — whether a
> cash-secured option is admissible — is deliberately **not** answered here and not answerable by
> anyone but the maintainer; see "What this record refuses to do".

## Context

#637 exists so that "does keel trade options" is decided **once, explicitly, with facts, never by
drift**. It names three collisions:

1. **The cash rail is shipped code.** `verify_cash_account` refuses any `/v2/account` `multiplier`
   but `1` at broker construction (#372,
   [`adapter.py:262`](../../packages/keel-broker-alpaca/keel_broker_alpaca/adapter.py)). Options are
   multiplier-100 instruments; the engine refuses them by construction, today.
2. **Spot-only, no-leverage is the category stance** — the exact line refused when benchmarking
   Jesse's perpetual futures, margin and short-selling.
3. **The Shariah screen.** Conventional options are gharar-and-leverage instruments and enter the
   book non-admissible by default.

Its sequencing said the ADR is written only after two things land. **Both now have:** #370 shipped
the equities path, and #636's discovery closed on 2026-09-07 with its findings recorded in
[`docs/research/2026-09-07-alpaca-options-venue-reality.md`](../research/2026-09-07-alpaca-options-venue-reality.md)
(#753). So this record is due, and the default posture it would replace remains the current one:
cash-spot only.

## What the discovery found

Marked as that document marks itself — `[DOC]` quoted from Alpaca, `[DOC-GAP]` unanswered, `[KEEL]`
verified in this tree, `[ARITH]` derived. **No API call was made**; #636's own rule of engagement is
that the decision must not rest on documentation alone, and that rule is not satisfied.

**Three findings settle the near term without reaching the fiqh question at all.**

**F7 — the capital floor `[ARITH]`.** A cash-secured put is collateralised at `strike × 100` and
cannot be fractionalised. The cheapest plausible underlying needs **$1,800** per contract, against
`config.live.yaml`'s `budget_usd: 50`. That is **~36×** the deployment's DCA clip, and a covered
call needs 100 shares held outright — the same floor paid in stock. **At current deployment size
keel cannot open a single compliant options position on any ordinary underlying**, whatever anyone
concludes about permissibility.

**F5 — rail 19 is the charter `[KEEL]`.** An OCC symbol (`SPY250127C00608000`) has no hyphen, so
`parse_spot_product_id` returns `None` and rail 19 vetoes
([`guards.py:905`](../../keel/execution/guards.py)). The rail runs in **every mode, both sides, DCA
included** — deliberately outside `LIVE_STATE_RAILS` — so **paper cannot skip it and there is no
rehearsal path**: keel could not paper-trade an option to gather evidence without amending the rail
first. Its own comment states the stake: *"Spot-only is this agent's CHARTER, not an operator
preference, so there is no config field here to widen."*

**F1 — the venue posture is undocumented, and the downside is asymmetric `[DOC-GAP]`.** Alpaca
offers no cash accounts at all (*"All accounts are set up as margin accounts"*); keel's whole
posture is the `multiplier == 1` proxy reached via `max_margin_multiplier`. Whether options approval
**survives** that setting is stated nowhere. If enabling options moves the multiplier,
`verify_cash_account` raises and the adapter stops building — **breaking the equities trading that
already ships**. Options would then be a strictly destructive change to a working capability.

**F3 is the one that deserves argument on its merits `[DOC]`**, rather than triage: ITM contracts
auto-exercise at `$0.01`, and where buying power is short, *"Alpaca will sell-out the position
within 1 hour before expiry."* Assignment arrives by **polling only** — *"Options assignments are
not delivered through websocket events."* A venue that disposes of inventory on its own timetable,
and an inventory event with no originating keel order, is a different relationship to the book than
anything keel has accepted.

## Options

**A — Hold the line at cash-spot, and record what would reopen it.** Nothing ships. Rail 19, the
cash rail and the screen stand unamended. The question stops being re-asked because the answer is
written down with its triggers, in the shape ADR 0002 set.

*Costs:* forgoes covered calls as a yield-on-inventory story, which is the strongest halal-adjacent
argument for options and the one most often raised.
*Buys:* the charter stays a charter. No rail is widened for an instrument that is, today,
unexecutable at this size and unverified on this venue.

**B — Amend now, to the narrowest defensible slice.** Cash-secured only (covered calls,
cash-secured puts), exception-gated into an unscreened book surfaced as `posture: exceptions`, with
its own conformance-suite order kinds and the Strathern rail extended over the new surface.

*Costs:* amends rail 19 — the change #637 itself frames as constitutional. Level 1 needs no
multi-leg support, so the port change is genuinely small; the **rail** change is not. And it buys a
capability that cannot be exercised until the account is one to two orders of magnitude larger, so
the amendment would sit unused and unmeasured, which is the condition under which rails erode.

**C — Defer again, unresolved.** Rejected on its face: #637 exists precisely to stop this question
being re-litigated by drift, and a third deferral with no trigger is drift with a date on it.

## What this record refuses to do

**It does not answer whether a cash-secured option is admissible.** That is a fiqh-methodology
question — gharar, the nature of the premium, whether full collateralisation changes the character
of the contract — and constitution line 5 ("every attestation is human-sourced, or refused") means
it cannot be settled by a machine reading documentation. Option A below is drafted as *"not now, on
operational grounds, question expressly not reached"* precisely so that signing it does **not**
commit the project to a jurisprudential position it has not taken.

If the maintainer wants the fiqh question answered rather than side-stepped, that is a different
record, sourced the way `docs/research/2026-08-18-*` sourced its scholarship.

## Decision

> ⟨**UNSIGNED.** Strike one, keep the other, set Status to `decided`, and date it.⟩

**A — HOLD THE LINE.** keel does not trade options. Rail 19, the cash rail and the screen are
unamended. The refusal rests on **operational grounds that do not require the permissibility
question to be answered**: options are unexecutable at this deployment's size (F7), admitting them
means amending the rail that calls itself the charter (F5), and the venue may not permit them on the
posture keel requires — with the failure mode being loss of the equities path that already works
(F1). **The question of whether cash-secured options are permissible is expressly not reached.**

**B — AMEND.** keel admits cash-secured options under the exception ceremony, per the slice in
#637. Requires, before any code: a recorded human attestation on the permissibility question with
its sources; #636 reopened and closed against a real paper account rather than documentation; and a
capital plan under which a position is executable.

## Consequences

**If A is signed.** Nothing changes in the code — the value is that the refusal is recorded and
citable, so the next reader does not re-derive it. #636 stays closed. The three findings above
become the standing answer to "why not options", and the triggers below become the only route back.

**If B is signed.** Rail 19's grammar must widen to admit an instrument shape, and its comment must
stop calling itself the charter or the comment becomes false — the rail's own text is load-bearing
here, not decoration. The exception book needs its reporting story before any order kind lands.
`BrokerCapabilities` needs no multi-leg shape for Level 1, which is the one piece of good news.
`keel serve` remains credential-free either way; nothing here touches that boundary.

## The triggers that reopen this (for A)

Each is a fact, not a feeling, in ADR 0002's style:

1. **Deployment equity clears the floor by a real margin** — enough that a cash-secured put on an
   allowlist-eligible underlying is a normal-sized position rather than the whole account. F7's
   arithmetic is the test; `budget_usd` and the account are the inputs.
2. **F1 is answered on a live account** — Alpaca confirms in writing, or an application demonstrates,
   that options approval survives `max_margin_multiplier: 1`. **A "no" here closes this permanently**,
   not provisionally: it would mean options and keel's cash posture are mutually exclusive at this
   venue.
3. **The permissibility question is answered by a human, with sources**, in a record of its own.
   Without this, triggers 1 and 2 firing together still do not reopen B.
4. **F3 is judged acceptable** — a venue that auto-exercises and liquidates on its own timetable,
   and reports assignment only by polling, is compatible with keel's claim that nothing disposes of
   inventory but its own exit policy.

Triggers 1–3 are necessary together. Trigger 4 is a judgement to be made explicitly rather than
inherited.
