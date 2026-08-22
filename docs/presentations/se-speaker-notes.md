# Speaker notes — `se.md` (senior software engineers)

Companion to [`se.md`](se.md). Section numbers match. Not for distribution — this is the version
with the traps in it.

## Before you start

**Know your room.** This deck works for engineers who have never heard of Islamic finance. The
single biggest failure mode is spending the first ten minutes on religion. **Don't.** Lead with
the engineering problem — "how do you make a rule that software cannot be talked out of?" — and
let the fiqh arrive as a *source of requirements*, not as a subject.

**Have these open in tabs, in this order:** `keel/execution/guards.py`, `keel/agent.py`,
`keel/strategy/rules/base.py`, `packages/keel-broker-api/keel_broker_api/port.py`,
`keel/compliance/screen.py`. You will be asked to show code. Scrolling to find a file kills
momentum.

**Timing.** 60 min total: §1–5 in 20, §6 in 25, §7–10 in 15. Overrunning §6 is fine; overrunning
§1–5 is not.

**The 25-minute cut:** §1 (3) → §4 architecture diagram only (5) → §5.1 fail-closed (4) → §6.5
guards, the rails, nothing else (10) → §9 open problems (3). Drop the walkthrough's other six
files, drop §7 entirely. The rails are the thing worth their time.

---

## §1 — What this is

**Open with the concession, not the pitch.** "Plenty of people have a trading bot. That part is
not interesting and I'm not going to spend your time on it." You will lose a technical room
instantly if it smells like a product pitch, and you win credibility cheaply by conceding the
unremarkable part first.

**Land claim 3 hard: it doesn't make money.** Say it in the first two minutes, unprompted. It
inoculates you against the entire "so does it work?" line of questioning, and in a room of senior
engineers, volunteering your own negative result buys more trust than any architecture diagram.

**Expect early scepticism about crypto itself.** Someone will signal it, possibly by tone rather
than a question. Do not defend crypto. Say: "the asset class is not the interesting part of this
talk — substitute equities and every slide still holds." That is true (there is an Alpaca
adapter) and it moves you back to engineering.

---

## §2 — Stack

**Move fast here.** Two minutes. The table is for the deck, not for reading aloud. Call out three
things only:

1. **CI runs 3.11 and 3.14** — floor and actual, not one or the other.
2. **`Decimal` only.** Someone always nods at this.
3. **The absences.** Read the "deliberately absent" list aloud. It provokes better questions than
   the presence list, and you *want* those questions in Q&A rather than in the middle of §6.

**If asked "why not Postgres?"** — one account, one writer, one process. The DB is a file you can
copy, diff and attach to a bug report. Say you'll revisit it in §9.6 and move on.

---

## §3 — Repo topology

**The `commands/` number is your credibility moment.** 23,188 lines, larger than the engine. Put
it up and *volunteer* that it's uncomfortable before anyone points it out. "That's the number I
expect one of you to push back on, and it's on the open-problems list." A presenter who names
their own worst metric is trusted for the next fifty minutes.

**The fake broker usually gets a laugh or a raised eyebrow.** Shipping a distribution that exists
only to constrain a design is unusual. Have the reason ready: a port with one implementation is
not a port, it's an indirection. The fake is what makes it a real seam.

---

## §4 — Ports and adapters

**This is where you must not oversell.** The migration is unfinished, and someone will find
`_common.py` if you claim otherwise. Say it on the slide, out loud, early: "four adapters exist
and pass conformance in CI; the live path still constructs Coinbase directly. The port is real,
the rewiring is not done."

Presenting an aspiration as an achievement is the fastest way to lose a senior room, and this
detail is exactly the kind they find.

**`BrokerCapabilities` is the part worth extra time** if the room is architecture-minded. The
usual approach is a lowest-common-denominator interface or a pile of `NotImplementedError`.
Declaring capabilities and adapting the flow is a genuinely different choice. Ask them what they'd
have done — it's a good two-minute detour and it makes the talk a conversation.

**`Protocol` vs ABC** will come up. Answer: adapters carry no runtime import of the port,
structural typing, no inheritance coupling. If someone argues ABCs give better error messages,
concede it — they're right, and it was a trade.

---

## §5 — The three invariants

**§5.1 is the intellectual core of the whole talk.** Slow down. The fail-closed table is the
single best slide in the deck because every row is a case where the obvious engineering choice is
the *wrong* one.

Use this framing: **"most systems treat missing data as a retry. This one treats it as a
refusal."** Then the kill-switch example — `default=True` on a *get* is the kind of one-character
decision that carries the whole design.

**Expect the objection: "that'll block everything."** The answer is yes, and it's on the slide in
§10. The engine currently refuses to trade. Agreeing with the objection is stronger than
defending against it.

**§5.2 — keep it short and structural.** COMPUTED vs ATTESTED. Do not get drawn into what `'ayn`
and `dayn` mean; if pushed, "owned thing versus a claim on an issuer" is enough. The engineering
point is provenance-as-a-data-requirement, and it lands for anyone who has done audit or
compliance work in any domain.

**§5.3 — the riba-compounding-into-position-size example is the best story in the deck.** A
religious constraint and a numerical-correctness constraint turning out to be the *same*
constraint. Tell it as a story: interest accrues in the balance → balance feeds equity → equity
feeds the sizing formula → the forbidden thing is now silently scaling your risk. Rooms remember
this one.

---

## §6 — The walkthrough

**Announce the structure before you start:** "one hypothetical buy, seven files, in the order the
code touches them." Without that frame it reads as a file tour and people disengage.

**Actually open the files.** Screenshots of code in slides are a tell that the presenter doesn't
know the codebase. You do — use the editor.

**§6.3 — the point to make is what `detect()` *cannot* see.** No account, no balance, no venue. A
rule is physically incapable of sizing a position. If the room is going to steal one idea, this
should be it, and it transfers to any domain: the component that decides *what* must not know
*how much*.

**§6.5 is the destination.** Budget ten minutes and protect them. Four decisions, in order of how
much they impress:

- *No broker access, by design* — leads to "the whole rail suite tests with no network." This
  gets the strongest reaction from anyone who has fought mock-server fixtures.
- *Never short-circuits* — collect all failures. Obvious in hindsight, rarely done.
- *`offline=True` skips exactly two named rails* — read the reasoning out loud. It's a subtle
  argument about not corrupting your own evidence, and it shows the codebase thinks.
- *Un-overridable* — no `force=True`. Let it sit for a second.

**Rail 17 is your closing image for this section.** "A rule about physical possession of goods,
written centuries before computers, is in this codebase as a seven-day TTL on an attestation."
Then move on — don't over-explain it. The image does the work.

**If time is short, cut 6.2, 6.4 and 6.7.** They are context, not content.

---

## §7 — Research side

**The next-bar-open fill is the honest-engineering story.** The backtest fills the way the
executor actually places. Consequence: strategies that assume a resting limit order are
*mis-modelled by construction*, and the codebase says so rather than quietly producing better
numbers.

**Have the concrete number ready:** `pullback_continuation`'s gross profit factor fell from 0.92
to 0.77 when the fill model was corrected, because a market fill takes trades the strategy meant
to decline. A specific number beats the general principle.

**Then the punchline: cost is the binding constraint.** ~2.5% round-trip against a per-trade edge
of the same order of magnitude. This reframes the whole project from "trading system" to "a study
of whether the edge survives the toll" — which is a more interesting thing to have built, and a
more honest description.

---

## §8 — Testing

**Lead with the cautionary tale, not the counts.** Two defects produced plausible, internally
consistent, *wrong* output for the life of the project while 2,712 tests passed. Nobody in a
senior room cares about your test count; everybody cares about the failure mode where the suite is
green and the numbers are fiction.

The takeaway to hand them: **"ask what your numbers cannot distinguish."** That's the reusable
idea and it belongs to them after this talk.

**On counts, be precise:** 766 test functions in 41 top-level files; parametrisation expands the
executed count. Don't quote a big round number you can't source — this room will ask how you
counted.

---

## §9 — Open problems

**This section is the point of presenting to engineers at all**, so don't let it get squeezed.
Reserve three minutes minimum.

Frame it as a request, not a disclosure: "six things I have not solved and would genuinely like
opinions on." Then be quiet. The silence is productive; let someone else fill it.

**Most likely to start a real discussion:** #1 (migration sequencing — everyone has a flag-day
scar) and #2 (the `commands/` size). **#5 is the deepest** — a `Rule` interface that cannot express
a conditional entry is a genuine design limitation with no cheap fix, and if someone engages with
it they are the person to keep talking to afterwards.

---

## §10 — Q&A

The listed questions are pre-loaded because a room that asks nothing is a room that didn't follow.
If the first thirty seconds are silent, ask one of them *yourself* and answer it. That reliably
breaks the ice.

### Traps, and how to handle them

**"Isn't this just a config file with extra steps?"** — Fair and sharp. The difference is
attribution and immutability of the audit trail: a config value is a setting; an attestation is a
recorded claim with a named source, and the engine refuses without one. Concede that the line is
thinner than it looks.

**"How do you know the rails actually fire?"** — They're tested for behaviour under missing input,
and every veto is written to the DB with a reason. Then be honest: the rails are tested; the
*completeness* of the rail set is a judgement, not a proof.

**"Why would anyone use software to enforce their own religious rules?"** — This can be asked
hostilely or sincerely; answer it the same either way. The software refuses to let *you* make a
convenient exception at the moment you most want to. Nobody's conduct is governed but the
operator's own. Don't be defensive; it's a reasonable question.

**"Has a scholar reviewed this?"** — No, and the README says so before anything else. Say it
flatly. Any hedging here costs you everything §1 bought.

**If someone finds a real bug during the talk — thank them, write it down visibly, and move on.**
Do not debug live. Getting a genuine finding from a walkthrough is a *success* of the format;
treat it as one.
