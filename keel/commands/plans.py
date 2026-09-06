"""The Plans page, inverted -- a transparency artifact, not a paywall matrix (#706).

Alpaca's Plans & Features page is a tier matrix with "Current plan" and "Upgrade" badges. It is
radically clear about pricing and it is honest *as marketing*: every row exists to move a reader
one row down. Jesse paywalls even paper trading.

keel's constitution runs the other way -- "Paper is free and unlimited, forever" and "The free
engine is never a demo" are two of its eight numbered lines. So this page takes the clarity and
refuses the gating. It states what runs on this device and why it costs nothing, what a tier would
change if one ever existed, and the named trigger that must fire before any of it does.

── EVERY CLAIM CARRIES THE DOCUMENT IT CAME FROM, AND A TEST READS THAT DOCUMENT ────────────────

The acceptance criterion is traceability, so it is mechanical rather than asserted. A page of
marketing sentences that merely SOUNDED like the project's documents would pass a human reviewer
and rot within a month; the sentences here are quoted verbatim and `tests/commands/test_plans.py`
opens each cited file and looks for them.

That check runs in both directions, and the second one is why it is worth having. A claim nobody
can trace fails the build -- but so does a claim the project has since edited out of its own
documents, which is the failure that would otherwise go unnoticed: a promise page still making a
promise nobody kept.

── NOTHING HERE IS FOR SALE, AND THE PAGE SAYS SO RATHER THAN IMPLYING IT ───────────────────────

ADR 0004's answer is "not now" and none of its four triggers has fired. No tier below is
`shipped`. There is no price a reader can act on, no address, no link and no instruction --
because a page that told someone how to pay would have shipped the tier. If a tier ever does
ship, its `shipped` flag is flipped by the PR that ships it, never ahead of it.

── AND NO DATABASE ──────────────────────────────────────────────────────────────────────────────

This page describes the project, not the deployment. It reads no repository, no config and no
network, which is why its route can answer on a machine with nothing set up at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

#: The two documents every claim on this page is quoted from.
EVOLUTION_PLAN = "docs/evolution-plan.md"
DECISION_RECORD = "docs/decisions/0004-monetisation-not-now.md"


@dataclass(frozen=True)
class Claim:
    """One quoted sentence and the file it came from.

    `source` is a repository-relative path rather than a title, so the test that checks this can
    OPEN it. A citation naming "the evolution plan" would be a citation nothing could verify --
    worse than none, because it looks checkable.
    """

    text: str
    source: str


@dataclass(frozen=True)
class Tier:
    """One row of the Phase F table, as published.

    `shipped` is `False` on every row and is the only field a future PR should change. It exists
    as a field rather than being implied by the page's prose because "this does not exist yet" is
    the single most important thing this table says, and a sentence can be edited past without
    anything failing.

    `available_now` is separate and true of exactly one row: the free tier is what the reader is
    already running. A table that presented four rows as four CHOICES would be a paywall matrix
    with the prices greyed out.
    """

    name: str
    price: str
    buys: str
    promise: str
    source: str
    shipped: bool = False
    available_now: bool = False


@dataclass(frozen=True)
class Trigger:
    """One of ADR 0004's four triggers -- what would have to happen before a tier exists.

    The record says the four are the complete set and that a new trigger is a new decision record
    rather than an edit, so this page may not grow a fifth.
    """

    number: int
    text: str
    source: str = DECISION_RECORD


#: The constitution, §4 of the evolution plan, verbatim and entire.
#:
#: ALL EIGHT. A page that quoted seven would be choosing which of the project's own commitments to
#: show a reader, on the page whose whole subject is what the project promises -- and the test
#: counts the numbered lines in the document rather than against a literal, so a ninth line fails
#: the build until this carries it.
CONSTITUTION: tuple[Claim, ...] = (
    Claim("A tool that cannot say no is a flattery tool.", EVOLUTION_PLAN),
    Claim("Scores report and gate; they never rank (the Strathern rail).", EVOLUTION_PLAN),
    Claim("Fees are priced at what was actually paid.", EVOLUTION_PLAN),
    Claim("Paper is free and unlimited, forever.", EVOLUTION_PLAN),
    Claim("Every attestation is human-sourced, or refused.", EVOLUTION_PLAN),
    Claim("The free engine is never a demo.", EVOLUTION_PLAN),
    Claim("The operator's servers never hold venue keys.", EVOLUTION_PLAN),
    Claim("Negative results are published.", EVOLUTION_PLAN),
)

#: Phase F's tier table, as a published intention. Nothing here is buyable.
TIERS: tuple[Tier, ...] = (
    Tier(
        name="Free",
        price="$0, forever",
        buys="The full engine. Never a demo.",
        promise="We see nothing",
        source=EVOLUTION_PLAN,
        available_now=True,
    ),
    Tier(
        name='Pro — "your box, anywhere"',
        price="$14/mo · $140/yr prepay",
        buys=(
            "managed `keel link`, signed+notarized installers (the #438 certs become Pro "
            "value), managed updates, Web Push plumbing, priority support"
        ),
        promise="We can't see your data",
        source=EVOLUTION_PLAN,
    ),
    Tier(
        name="Founder — lifetime",
        price="$399, first 200, then gone",
        buys="Everything in Pro, forever, founder badge",
        promise="Grandfathering is a promise, not a promo",
        source=EVOLUTION_PLAN,
    ),
    Tier(
        name='Hosted-confirm — "our brain, your keys"',
        price="$49/mo · $450/yr (built last)",
        buys="Full hosted engine; your device holds the keys and approves every order",
        promise="We can see, we can't act",
        source=EVOLUTION_PLAN,
    ),
)

#: How ADR 0004 frames what would reopen the question. Quoted because the framing IS the claim:
#: a trigger measured in revenue would make this page a countdown, and one measured in users makes
#: it a condition a reader can check for themselves.
TRIGGER_FRAMING = "Framed in users and demonstrated demand"
TRIGGER_FRAMING_SOURCE = DECISION_RECORD

#: The four triggers, abbreviated to their firing condition. The record's own table carries the
#: evidence each one needs and who observes it; this page carries what would have to be true.
TRIGGERS: tuple[Trigger, ...] = (
    Trigger(1, "keel reaches an audience shaped like Jesse's pre-2021 one"),
    Trigger(
        2,
        "The headline finding reverses — a shipped rule family is measured net-positive at the "
        "taker fee actually paid",
    ),
    Trigger(
        3,
        "Operators repeatedly and unprompted ask for something that costs real money to run on "
        "their behalf",
    ),
    Trigger(
        4,
        "keel's own maintenance load demonstrably exceeds what volunteer, spare-time work can "
        "carry",
    ),
)

#: What no tier will ever gate. The load-bearing half of this page, and the half a later
#: contributor would be most tempted to soften -- so each line is quoted from a document that
#: already committed to it rather than written fresh here.
NEVER_PAYWALLED: tuple[Claim, ...] = (
    Claim("Paper is free and unlimited, forever.", EVOLUTION_PLAN),
    Claim("Fees are priced at what was actually paid.", EVOLUTION_PLAN),
    Claim("Every attestation is human-sourced, or refused.", EVOLUTION_PLAN),
    Claim(
        "Live order placement (`keel/execution/`) stays inside the Apache-2.0 tree in full, "
        "always.",
        DECISION_RECORD,
    ),
    Claim(
        "**No engine feature gates for paid tiers** — the free engine is never a demo.",
        "docs/architecture.md",
    ),
    Claim(
        "**Affiliate or referral links to any trading venue, broker, or exchange are never "
        "added**",
        DECISION_RECORD,
    ),
)

#: What the page says about today, in one sentence. Held here rather than in the renderer for the
#: usual reason (Rule 2): it is a judgement about the project's state, and judgements are made in
#: Python.
NOTHING_FOR_SALE = (
    "Nothing on this page is for sale. keel is free, entirely, and no tier below exists — "
    "the prices are a published intention, not an offer."
)


def every_claim() -> Iterator[Claim]:
    """Every traceable claim on the page, for the test that checks each cited file exists."""
    yield from CONSTITUTION
    yield from NEVER_PAYWALLED
    for tier in TIERS:
        yield Claim(tier.promise, tier.source)
    for trigger in TRIGGERS:
        yield Claim(trigger.text, trigger.source)
    yield Claim(TRIGGER_FRAMING, TRIGGER_FRAMING_SOURCE)


@dataclass(frozen=True)
class PlansReport:
    now_ts: int
    constitution: tuple[Claim, ...]
    tiers: tuple[Tier, ...]
    triggers: tuple[Trigger, ...]
    never_paywalled: tuple[Claim, ...]

    @property
    def anything_for_sale(self) -> bool:
        """Whether any tier has actually shipped.

        Derived from the table rather than from a constant, so flipping one `shipped` flag is all
        it takes for the page to stop saying nothing is for sale -- and so that the sentence and
        the table can never disagree.
        """
        return any(tier.shipped for tier in self.tiers)


def gather_plans(*, now_ts: int) -> PlansReport:
    """The page. No repository, no config, no network -- it describes the project, not the
    deployment, which is why its route answers on a machine with nothing set up."""
    return PlansReport(
        now_ts=now_ts,
        constitution=CONSTITUTION,
        tiers=TIERS,
        triggers=TRIGGERS,
        never_paywalled=NEVER_PAYWALLED,
    )
