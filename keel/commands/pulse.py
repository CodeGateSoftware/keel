"""The console's Pulse home view, as a service -- the glance page's few new judgements.

A VIEW over `StatusReport`, `ActivityFeed` and `existing_dca_rules`: it reads nothing the CLI
cannot and computes only what no existing report holds -- today's sums, the why-not list, the
next DCA cadence day, the unbracketed roll-call. The halt is not here: `keel/web/gates.py` owns
it ("stopping must never be slower than starting"), and this module only names the command that
undoes it (`RESUME_COMMAND`), so the client never has to.

**A pure service: no click, no clock** (the split `keel/commands/dca_plan.py` documents). `now_ts`
is the caller's one read of the clock, and the activity feed arrives already scoped to "today" --
this module never decides which day it is. `gather_pulse` is the seam `keel/web/api.py` calls;
`build_pulse` is the same composition over already-gathered inputs, which is what lets a test
hand it a report without standing up a ledger.

**Why-not is a list of the engine's own sentences, never a diagnosis.** Every entry is either a
standing blocker this module reads off `StatusReport` (kill switch, rail 11, rail 17) or a
highlight `activity.summarise_cycle` already wrote for a cycle (`_WHY_PREFIXES`). Nothing here
re-derives a rail's verdict: a reason that is not in the log or the report is not offered.

**The next DCA buy is a cadence DAY, not an order time.** `Dca.detect` keys the buy off the
COMPLETED daily bar's day (`is_cadence_day`); the order is placed by the first cycle that sees
that bar, and when that cycle runs is the scheduler's business, which this module does not know.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.commands.activity import ActivityFeed
from keel.commands.dca_plan import ExistingDcaRule, existing_dca_rules
from keel.commands.status import OpenPositionStatus, StatusReport, gather_status
from keel.commands.trading import KILL_ENGAGED_LINE
from keel.config import Config
from keel.data.repository import Repository
from keel.strategy.rules.dca import next_cadence_day_ts

#: What undoes a halt. The CLI's own command name; the console shows it and never performs it.
RESUME_COMMAND = "keel resume"

#: The `activity.summarise_cycle` highlights that say why an entry did not happen -- matched on
#: their leading words, the way that function writes them. `ENTERED`/`EXITED`/`error:`/`external
#: flow` highlights are notable but are not reasons, so they are not here.
_WHY_PREFIXES = (
    "rail veto: ",
    "not placed: ",
    "gate rejected: ",
    "entries withheld",
    "cycle skipped: ",
    "stale feed: ",
)


#: The attestation states rail 17 halts entries on -- the ones `status._rail17_line` renders
#: with "entries halted". "attested" passes; "unknown" is unreadable, and status says so itself.
_RAIL17_HALTING = frozenset({"suspended", "expired", "unattested"})


@dataclass(frozen=True)
class TodayCounts:
    """The scoped cycles' five counts (plus the cycle count), summed. `blocked` is the wide one
    `summarise_cycle` documents: entries withheld, rail-vetoed or otherwise not placed.

    Every figure is `None` when the log could not be read (`today_basis == "unavailable"`): "not
    recorded" is not "recorded as zero", and summing nothing into six zeros is the always-passing
    fee rail's shape (#198)."""

    cycles: int | None
    signals: int | None
    blocked: int | None
    entered: int | None
    exited: int | None
    errors: int | None


@dataclass(frozen=True)
class NextDcaBuy:
    rule_id: int
    product_id: str
    cadence_days: int
    budget_usd: Decimal
    #: 00:00 UTC of the cadence day (`dca.next_cadence_day_ts`) -- a day, not an order time.
    next_cadence_day_ts: int
    #: `ExistingDcaRule.budget_inferred`, carried through: the budget is the rule's default, not
    #: one the row stored.
    budget_inferred: bool


@dataclass(frozen=True)
class PulseReport:
    now_ts: int
    status: StatusReport
    #: Already scoped to "today" by the caller (`activity.apply_scope`).
    feed: ActivityFeed
    today: TodayCounts
    #: How much of the day `today` and `why_not` cover: `"complete"` (the log was read back past
    #: midnight, or it is a real empty file), `"partial"` (the bounded read began after midnight,
    #: so the sums are a lower bound -- `ActivityFeed.scope_fully_covered`) or `"unavailable"`
    #: (the log could not be read at all). Judged HERE, because the empty-reasons sentence below
    #: is only true for `"complete"`.
    today_basis: str
    #: The sentence for an empty `why_not`, worded for `today_basis`. Sent so the client never
    #: infers "nothing stopped keel" from the emptiness of a list.
    why_not_none: str
    #: The line for a Pulse with no cycle today, worded for `today_basis`: `""` when a cycle ran
    #: (nothing to say), when the log could not be read (the status card already says so, and
    #: "keel has not run" would be an engine claim the log never made) -- else the sentence. The
    #: client shows its card on the PRESENCE of this string, as the TUI says
    #: `activity.describe_empty_scope` only when the read was fine.
    no_cycles_line: str
    #: Newest cycle today, else the last one before the scope, else None (keel has no log yet).
    last_cycle_ts: float | None
    #: Ordered, de-duplicated sentences: standing blockers first, then today's cycle reasons.
    why_not: tuple[str, ...]
    #: LIVE `dca` rules only, in rule-id order.
    next_dca: tuple[NextDcaBuy, ...]
    #: Live-mode tranches whose bracket order is absent. Empty in paper, which never has one by
    #: construction (the judgement `payload._bracket_field` already makes).
    unbracketed: tuple[OpenPositionStatus, ...]
    #: `len(unbracketed)`, taken HERE: `payload.py` never counts (its Rule 6e).
    unbracketed_count: int
    #: `RESUME_COMMAND` when the kill switch is engaged, else "". Its presence is the client's
    #: only branch between a HALT button and a HALTED line.
    resume_command: str


#: `ActivityFeed.status` values whose `cycles` are a real reading of the log (an `empty` file is
#: a true zero). Every other status -- missing, unreadable, unparseable, oversized -- means the
#: log says nothing about today.
_READ_STATUSES = frozenset({"ok", "empty"})

_NOTHING_STOPPED = "Nothing stopped keel today."
_WHY_NOT_NONE = {
    "complete": _NOTHING_STOPPED,
    "partial": (
        "No reason in the part of today's log that was read, and the log may not reach "
        "back to midnight, so reasons may be missing."
    ),
    "unavailable": "Today's log could not be read, so reasons may be missing.",
}


_NO_CYCLES_LINE = {
    "complete": "keel has not run yet today.",
    "partial": "No cycle in the part of today's log that was read.",
    "unavailable": "",
}


def _today_basis(feed: ActivityFeed) -> str:
    if feed.status not in _READ_STATUSES:
        return "unavailable"
    if not feed.scope_fully_covered:
        return "partial"
    return "complete"


def _today_counts(feed: ActivityFeed) -> TodayCounts:
    if feed.status not in _READ_STATUSES:
        return TodayCounts(
            cycles=None, signals=None, blocked=None, entered=None, exited=None, errors=None
        )
    cycles = feed.cycles
    return TodayCounts(
        cycles=len(cycles),
        signals=sum(c.signals for c in cycles),
        blocked=sum(c.blocked for c in cycles),
        entered=sum(c.entered for c in cycles),
        exited=sum(c.exited for c in cycles),
        errors=sum(c.errors for c in cycles),
    )


def _last_cycle_ts(feed: ActivityFeed) -> float | None:
    if feed.cycles:
        return max(c.started_ts for c in feed.cycles)
    if feed.last_cycle_before_scope is not None:
        return feed.last_cycle_before_scope.started_ts
    return None


def _why_not(status: StatusReport, feed: ActivityFeed) -> tuple[str, ...]:
    """Standing blockers, then every reason today's cycles recorded, newest cycle first, each
    sentence once. Rail 17 is named in every mode but paper, for every state that
    `status._rail17_line` says halts entries (suspended, expired, never attested): it is a
    LIVE_STATE rail, not evaluated in paper, so a lapsed attestation there halts nothing."""
    reasons: list[str] = []
    if status.kill_switch_engaged:
        reasons.append(KILL_ENGAGED_LINE)
    if status.rail11_status == "HALTED":
        reasons.append(f"rail 11: {status.rail11_status}")
    attestation = status.withdrawal_attestation.state
    if status.mode != "paper" and attestation in _RAIL17_HALTING:
        reasons.append(f"rail 17: withdrawal attestation {attestation}")
    # `feed.cycles` is newest-first (`feed_from_lines`), so iteration order is the order asked for.
    for cycle in feed.cycles:
        for highlight in cycle.highlights:
            if highlight.startswith(_WHY_PREFIXES) and highlight not in reasons:
                reasons.append(highlight)
    return tuple(reasons)


def _next_dca(rules: tuple[ExistingDcaRule, ...], now_ts: int) -> tuple[NextDcaBuy, ...]:
    return tuple(
        NextDcaBuy(
            rule_id=r.rule_id,
            product_id=r.product_id,
            cadence_days=r.cadence_days,
            budget_usd=r.budget_usd,
            next_cadence_day_ts=next_cadence_day_ts(now_ts, r.cadence_days),
            budget_inferred=r.budget_inferred,
        )
        for r in rules
        if r.status == "live"
    )


def build_pulse(
    status: StatusReport,
    feed: ActivityFeed,
    dca_rules: tuple[ExistingDcaRule, ...],
    *,
    now_ts: int,
) -> PulseReport:
    """`gather_pulse`'s composition over inputs already in hand. PURE."""
    unbracketed = (
        ()
        if status.mode == "paper"
        else tuple(p for p in status.open_positions if not p.has_bracket)
    )
    basis = _today_basis(feed)
    return PulseReport(
        now_ts=now_ts,
        status=status,
        feed=feed,
        today=_today_counts(feed),
        today_basis=basis,
        why_not_none=_WHY_NOT_NONE[basis],
        no_cycles_line="" if feed.cycles else _NO_CYCLES_LINE[basis],
        last_cycle_ts=_last_cycle_ts(feed),
        why_not=_why_not(status, feed),
        next_dca=_next_dca(dca_rules, now_ts),
        unbracketed=unbracketed,
        unbracketed_count=len(unbracketed),
        resume_command=RESUME_COMMAND if status.kill_switch_engaged else "",
    )


def gather_pulse(
    repo: Repository, config: Config, feed: ActivityFeed, *, now_ts: int
) -> PulseReport:
    """The Pulse view's report: `gather_status` and `existing_dca_rules` read from the ledger,
    `feed` (scoped to "today" by the caller) read from the log. No broker, no network."""
    return build_pulse(
        gather_status(repo, config, now_ts), feed, existing_dca_rules(repo), now_ts=now_ts
    )
