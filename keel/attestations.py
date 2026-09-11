"""When each attestation expires, and how long is left -- the one place that knows (#793).

Live stopped placing orders on 2026-08-25 and nobody noticed for seventeen days. The exposure
caps were the primary cause, but rail 17's attestation expired on 09-08 and stacked on top, and
**nothing anywhere said so**. keel fails closed on a stale attestation, which is correct; it
failed closed SILENTLY, which is the hazard. An operator had to remember a 7-day TTL, or read
`guards.check_failed` records, to learn the agent was paused.

── WHY A MODEL AND NOT THREE WARNINGS ────────────────────────────────────────────────────────

Before this module there were four TTL constants in four places, across two incompatible shapes,
and one of them was a copy that claimed not to be:

    executor.WITHDRAWAL_ATTESTATION_TTL_SEC   7d    rail 17, freshness derived at read time
    cash_posture.ATTESTATION_TTL_SEC         90d    rail 22, due date STORED at attest time
    executor.BASE_INCREMENT_TTL_SEC           7d    sizing
    doctor.TTL_SEC                            7d    a duplicate, under the comment
                                                    "doctor only READS it" -- it did not

A banner, a doctor state and a webhook built on that would have been a fifth copy. So the model
comes first: every attestation reduces to the same two facts -- **when it was made, and when it
stops counting** -- and every surface renders those without computing either.

The two shapes are preserved rather than converted. Rail 17 derives its expiry from the TTL the
executor declares; rail 22 reads `attest_due_ts`, which is written at attest time because "a
record with no due date is a claim that never expires, which this record does not permit". This
module reads each the way its own rail does, and returns one shape.

── FOUR STATES, AND THE TWO ABSENCES ARE DIFFERENT ───────────────────────────────────────────

`MISSING` is not `EXPIRED`. `_withdrawals_enabled` already keeps them apart -- "nobody has
checked recently" is not the claim "the broker says withdrawals are suspended" -- and a banner
that called a never-attested rail "expired" would send an operator hunting a lapse that never
happened.

`EXPIRING` is the state that did not exist. An attestation was healthy until the instant it was
refused, so the only warning was the halt itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from keel_core.cash_posture import ATTESTATION_TTL_SEC as _POSTURE_TTL_SEC

#: The default warning window, and the one rail 17 uses. 48 hours: long enough that a daily agent
#: run and a daily doctor run each get a chance to say so before the halt, short enough that the
#: warning is not background noise for most of a 7-day TTL.
#:
#: **Per-attestation and not global, which the first draft got wrong.** A single window looks
#: tidy until it meets rail 22, whose TTL is 90 days: 48 hours' notice on a quarterly attestation
#: is no notice at all, and `doctor` already knew that -- it warns rail 22 at
#: `ATTESTATION_TTL_SEC // 6` and says why, "proportional to" a fixed TTL. The window belongs to
#: the attestation, so each keeps the schedule its own rail reasoned itself into.
WARN_WITHIN_SEC = 48 * 3_600

#: Nobody has ever attested this. Distinct from `EXPIRED` -- see the module note.
MISSING = "missing"
#: Attested, fresh, and not near its expiry.
OK = "ok"
#: Attested and inside its final `WARN_WITHIN_SEC`. The rail still permits entries.
EXPIRING = "expiring"
#: Past its expiry. The rail is vetoing now.
EXPIRED = "expired"

#: Attested, unexpired, and the answer was NO. Not an expiry and not an absence: rail 17 tells
#: the two apart in the operator's own words -- "nobody has checked recently" is not the same
#: claim as "the broker says withdrawals are suspended" -- and a surface that reported this one
#: as `ok` would be green while every BUY was refused. `keel doctor` was, until #793.
REFUSED = "refused"


@dataclass(frozen=True)
class Attestation:
    """One attestation, its dates, and what refreshes it."""

    #: Stable identifier a surface can key on.
    key: str
    #: The rail that vetoes when this is not fresh.
    rail: str
    #: What to call it on screen.
    label: str
    #: The command that refreshes it. A TTY command: rails 17 and 22 are Tier 2 and deliberately
    #: not admitted to the browser, so a cockpit banner has to TELL the operator what to type --
    #: an alarm with no action is worse than the silence it replaces.
    remedy: str
    #: When the attestation was made, or `None` if it never was.
    attested_at: int | None = None
    #: When it stops counting, or `None` if it was never made.
    expires_at: int | None = None
    #: How long before `expires_at` this one starts warning. Proportional for a long TTL, flat
    #: for a short one -- see `WARN_WITHIN_SEC`.
    warn_within_sec: int = WARN_WITHIN_SEC

    #: The rail's own verdict where the rail has one -- the question the CLOCK does not answer.
    #: Rail 17's attestation records an ANSWER as well as a date (`withdrawals_enabled`), and
    #: `False` halts entries exactly as an expiry does; rail 22's record can be REFUTED by venue
    #: evidence or attest a MARGIN-ENABLED account, both of which halt entries with the due date
    #: still months away. An earlier draft left rail 22 `True` on the grounds that doctor already
    #: reported its verdict and a notification already carried it -- which was true until #793
    #: began suppressing that notification against a window this field is part of.
    satisfied: bool = True

    @property
    def effective_warn_sec(self) -> int:
        """The window, never longer than the life of the attestation it guards.

        `withdrawal_expiry` reads the rail's TTL at call time so that changing it moves every
        surface at once -- but the 48-hour window is flat, so a TTL shortened below 48 hours
        (the expiry test sets it to three days; a future rail could be tighter still) would make
        every fresh attestation `EXPIRING` on the instant it was made, and the banner would be
        permanently lit. Capped at half the TTL, so there is always an `OK` half.
        """
        if self.attested_at is None or self.expires_at is None:
            return self.warn_within_sec
        return min(self.warn_within_sec, max(1, (self.expires_at - self.attested_at) // 2))

    def state(self, now_ts: int) -> str:
        """`MISSING`, `OK`, `EXPIRING` or `EXPIRED` at `now_ts`, on THIS attestation's window."""
        if self.attested_at is None:
            return MISSING
        # Attested, with no expiry. `VenueCashPosture` refuses to invent one and rail 22 refuses
        # to treat the record as never expiring, so neither does this -- and `MISSING` would be
        # the wrong word twice over: it names a rail NOBODY has attested, and this one was.
        if self.expires_at is None:
            return REFUSED
        if now_ts >= self.expires_at:
            return EXPIRED
        # The clock outranks the verdict, above, deliberately: an expired "no" is a stale
        # reading, and `expired` is the word that says so. Either way the remedy is the same
        # command, so nothing an operator does turns on the order -- only what they are told.
        if not self.satisfied:
            return REFUSED
        if self.expires_at - now_ts <= self.effective_warn_sec:
            return EXPIRING
        return OK

    def seconds_left(self, now_ts: int) -> int | None:
        """Seconds until expiry, floored at zero, or `None` when never attested.

        Floored, because a surface rendering a negative countdown says "expires in -3 days",
        which reads as a bug rather than as a halt.
        """
        if self.expires_at is None:
            return None
        return max(0, int(self.expires_at - now_ts))


def withdrawal_expiry(*, attested_at: int) -> int:
    """When a rail-17 attestation made at `attested_at` stops counting.

    Reads `executor.WITHDRAWAL_ATTESTATION_TTL_SEC` AT CALL TIME rather than importing the value
    into a module constant, so changing the rail's TTL changes every surface at once. That is the
    property `doctor.TTL_SEC` claimed and did not have, and `tests/test_attestation_expiry.py`
    mutates the constant to prove this one does.
    """
    from keel.execution import executor

    return int(attested_at) + int(executor.WITHDRAWAL_ATTESTATION_TTL_SEC)


#: Every attestation a rail can veto on, with no dates filled in. `survey` returns these with the
#: deployment's own dates attached. Separate so a surface can describe the SET without a database
#: -- and so `tests/test_attestation_expiry.py` can assert the set is exactly these two, which is
#: what makes adding a third rail an edit to a test that says why it was not covered.
_DEFINITIONS: tuple[Attestation, ...] = (
    Attestation(
        key="withdrawal",
        rail="17",
        label="Withdrawal capability",
        remedy="keel withdrawals attest --enabled",
    ),
    Attestation(
        key="cash_posture",
        rail="22",
        label="Cash-only spot posture",
        remedy="keel posture attest --spot-cash",
        # Proportional, and taken from the rail's own TTL rather than chosen here -- 15 days of a
        # 90-day claim. `doctor` picked this window first and reasoned it out; this reads the same
        # number rather than becoming a second opinion about it.
        warn_within_sec=_POSTURE_TTL_SEC // 6,
    ),
)


def survey_definitions() -> tuple[Attestation, ...]:
    """The attestations keel has, without reading a database."""
    return _DEFINITIONS


def definition(key: str) -> Attestation:
    """One dateless definition, for a surface that already holds the dates.

    `doctor` is that surface: it is handed `withdrawals_attested_at` by its caller and needs the
    window and the TTL, not another read of the same row.
    """
    for candidate in _DEFINITIONS:
        if candidate.key == key:
            return candidate
    raise KeyError(key)


def survey(repo: Any, now_ts: int, *, venue: str = "coinbase") -> Sequence[Attestation]:
    """Every attestation, with this deployment's dates attached.

    Never raises: a read that fails comes back as `MISSING`, which is what every rail already
    does with an unreadable attestation. A surface whose banner vanished because the database was
    briefly locked would be worse than one that says "nobody has attested".
    """
    readers = {
        "withdrawal": lambda: _withdrawal_dates(repo),
        "cash_posture": lambda: _posture_dates(repo, venue),
    }
    # By KEY, never by index. `_DEFINITIONS[0]`/`[1]` was the first spelling and it fails two
    # ways in silence: a third rail appended reaches `survey_definitions()` and never `survey()`,
    # so it can never be banner, doctor or ledger; and a REORDER attaches rail 17's dates to rail
    # 22's definition. A rail with no reader here surveys as `MISSING`, which is the safe answer
    # -- it says "nobody has attested" rather than "this is fine".
    return tuple(
        replace(definition, **readers[definition.key]())
        if definition.key in readers
        else definition
        for definition in _DEFINITIONS
    )


def _withdrawal_dates(repo: Any) -> dict[str, Any]:
    """Rail 17's dates, read the way `_withdrawals_enabled` reads them.

    `withdrawals_enabled` is checked as well as the timestamp, because the rail treats an
    attestation with no verdict as no attestation -- and a surface that showed a countdown for
    one would be counting down to the expiry of nothing.
    """
    try:
        attested_at = int(repo.get_state("withdrawals_attested_at", default=0) or 0)
        enabled = repo.get_state("withdrawals_enabled", default=None)
        if not attested_at or enabled is None:
            return {"attested_at": None, "expires_at": None}
        # INSIDE the `try`, with the reads. It was outside, which made "never raises" not quite
        # true -- and the caller that would have worn it is `notify_after_cycle`, whose single
        # broad `except` would have swallowed the whole cycle's notifications, `rail.armed` and
        # `setup.unplaced` included, for a fault in the attestation survey.
        return {
            "attested_at": attested_at,
            "expires_at": withdrawal_expiry(attested_at=attested_at),
            # `bool` of the stored value, matching `executor._withdrawals_enabled`'s last line.
            "satisfied": bool(enabled),
        }
    except Exception:
        return {"attested_at": None, "expires_at": None}


def _posture_dates(repo: Any, venue: str) -> dict[str, Any]:
    """Rail 22's dates, from the record's own STORED due date.

    Not re-derived from `ATTESTATION_TTL_SEC`: `attest_due_ts` is written at attest time and has
    no default precisely so a reader cannot quietly cover for a writer that forgot one. Deriving
    it here would be that cover.
    """
    try:
        record = repo.get_venue_cash_posture(venue)
        if record is None or record.attested_ts is None:
            return {"attested_at": None, "expires_at": None}
        return {
            "attested_at": int(record.attested_ts),
            "expires_at": None if record.attest_due_ts is None else int(record.attest_due_ts),
            "satisfied": _posture_holds(record),
        }
    except Exception:
        return {"attested_at": None, "expires_at": None}


def _posture_holds(record: Any) -> bool:
    """Whether rail 22 would accept this record on its own terms -- the CLOCK aside.

    The first version of `_posture_dates` read the dates alone, and `refute_posture` PRESERVES
    `attested_ts` and `attest_due_ts` (it records a refutation beside the claim rather than
    erasing it). So a refuted account surveyed as `ok`, with a due date months out, while rail 22
    vetoed every live entry -- and because `ok:<due_ts>` is a window that does not move, the
    notification was sent once and then suppressed for the rest of that due date.

    Deliberately the SAME two questions doctor asks, in doctor's own order (`doctor.py:484`,
    `:498`): venue evidence outranks the claim, and a MARGIN-ENABLED claim is a true answer that
    still halts entries. Restating them is the cost of the model not importing doctor; the
    `Attestation.satisfied` docstring's older note that rail 22 "leaves this True" is wrong and
    is corrected there.
    """
    from keel_core.cash_posture import MARGIN_ENABLED, CashPostureState

    if record.state is CashPostureState.REFUTED:
        return False
    return record.attested_posture != MARGIN_ENABLED
