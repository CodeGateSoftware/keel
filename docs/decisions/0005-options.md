# 0005 — keel does not trade options; the permissibility question is not reached

Date: 2026-09-08 · Issue: #637 · Status: decided

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
it cannot be settled by a machine reading documentation. The decision below was written, and is
signed, as *"not now, on operational grounds, question expressly not reached"* precisely so that
signing it does **not** commit the project to a jurisprudential position it has not taken.

If the maintainer wants the fiqh question answered rather than side-stepped, that is a different
record, sourced the way `docs/research/2026-08-18-*` sourced its scholarship.

## Decision

**keel does not trade options.** Rail 19, the cash rail and the screen are
unamended. The refusal rests on **operational grounds that do not require the permissibility
question to be answered**: options are unexecutable at this deployment's size (F7), admitting them
means amending the rail that calls itself the charter (F5), and the venue may not permit them on the
posture keel requires — with the failure mode being loss of the equities path that already works
(F1). **The question of whether cash-secured options are permissible is expressly not reached.**

Signed by the maintainer on 2026-09-08. The alternative considered and not taken was to amend to
the narrowest slice — cash-secured only, exception-gated — which is Option B under **Options**
above; it is left there rather than deleted, because a decision record that shows only the chosen
branch is a record of an outcome and not of a decision.

## Consequences

**Nothing changes in the code.** No rail is amended, no order kind is added, no capability row
appears. `verify_cash_account`, rail 19 and the screen stand exactly as they were, and #636 stays
closed.

**The value is that the refusal is now citable.** The three findings above are the standing answer
to "why not options", so the next reader does not re-derive them and the question stops returning
by drift — which is the whole reason #637 exists.

**What was NOT decided is as load-bearing as what was.** This record refuses options for reasons
that would hold for a non-Muslim operator running keel on the same account: the position is
unexecutable at this size, the rail admits no rehearsal path, and the venue posture is unverified.
It takes no position on gharar, on the character of a fully collateralised premium, or on whether
full collateralisation changes the contract. **Anyone citing this record as keel's jurisprudential
answer on options is citing it wrongly**, and trigger 3 below is what would produce that answer.

## The triggers that reopen this

Each is a fact, not a feeling, in ADR 0002's style:

1. **Deployment equity clears the floor by a real margin** — enough that a cash-secured put on an
   allowlist-eligible underlying is a normal-sized position rather than the whole account. F7's
   arithmetic is the test; `budget_usd` and the account are the inputs.
2. **F1 is answered on a live account** — Alpaca confirms in writing, or an application demonstrates,
   that options approval survives `max_margin_multiplier: 1`. **A "no" here closes this permanently**,
   not provisionally: it would mean options and keel's cash posture are mutually exclusive at this
   venue.
3. **The permissibility question is answered by a human, with sources**, in a record of its own.
   Without this, triggers 1 and 2 firing together still do not reopen the question.
4. **F3 is judged acceptable** — a venue that auto-exercises and liquidates on its own timetable,
   and reports assignment only by polling, is compatible with keel's claim that nothing disposes of
   inventory but its own exit policy.

Triggers 1–3 are necessary together. Trigger 4 is a judgement to be made explicitly rather than
inherited.
