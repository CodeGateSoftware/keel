"""When each attestation expires, and how long is left -- the one place that knows (#793).

Live stopped placing orders on 2026-08-25 and nobody noticed for seventeen days. The exposure
caps were the primary cause, but an attestation expired on 09-08 and stacked on top, and
**nothing anywhere said so**. keel fails closed on a stale attestation, which is correct; it
failed closed SILENTLY, which is the hazard. An operator had to remember the TTL, or read
`guards.check_failed` records, to learn the agent was paused.

── WHY A MODEL AND NOT A WARNING PER SURFACE ─────────────────────────────────────────────────

Before this module there were four TTL constants in four places, and one of them was a copy that
claimed not to be. A banner, a doctor state and a webhook built on that would have been a fifth.
So the model comes first: every attestation reduces to the same two facts -- **when it was made,
and when it stops counting** -- and every surface renders those without computing either.

**Rail 17 left this model in #1010.** It used to be the first definition here, a 7-day
attestation whose TTL the executor declared. Rail 17 now reads the venue's own
`allow_withdrawals` on every buy, so there is nothing to renew and nothing to expire; the only
thing an operator still sets is a manual suspension, which has no date. What remains is rail 22,
whose due date is STORED at attest time because "a record with no due date is a claim that never
expires, which this record does not permit".

── FOUR STATES, AND THE TWO ABSENCES ARE DIFFERENT ───────────────────────────────────────────

`MISSING` is not `EXPIRED`. A banner that called a never-attested rail "expired" would send an
operator hunting a lapse that never happened.

`EXPIRING` is the state that did not exist. An attestation was healthy until the instant it was
refused, so the only warning was the halt itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from keel_core.cash_posture import ATTESTATION_TTL_SEC as _POSTURE_TTL_SEC

#: The default warning window. 48 hours: long enough that a daily agent run and a daily doctor
#: run each get a chance to say so before the halt.
#:
#: **Per-attestation and not global.** 48 hours' notice on rail 22's 90-day attestation is no
#: notice at all, and `doctor` already knew that -- it warns rail 22 at `ATTESTATION_TTL_SEC // 6`
#: and says why, "proportional to" a fixed TTL. The window belongs to the attestation, so each
#: keeps the schedule its own rail reasoned itself into.
WARN_WITHIN_SEC = 48 * 3_600

#: Nobody has ever attested this. Distinct from `EXPIRED` -- see the module note.
MISSING = "missing"
#: Attested, fresh, and not near its expiry.
OK = "ok"
#: Attested and inside its final `WARN_WITHIN_SEC`. The rail still permits entries.
EXPIRING = "expiring"
#: Past its expiry. The rail is vetoing now.
EXPIRED = "expired"

#: Attested, unexpired, and the answer was NO (rail 22: refuted, margin-enabled, or no due date).
#: Not an expiry and not an absence, and a surface that reported it as `ok` would be green while
#: every BUY was refused.
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
    #: The command that refreshes it. A TTY command: rail 22 is Tier 2 and deliberately not
    #: admitted to the browser, so a cockpit banner has to TELL the operator what to type -- an
    #: alarm with no action is worse than the silence it replaces.
    remedy: str
    #: When the attestation was made, or `None` if it never was.
    attested_at: int | None = None
    #: When it stops counting, or `None` if it was never made.
    expires_at: int | None = None
    #: How long before `expires_at` this one starts warning. Proportional for a long TTL, flat
    #: for a short one -- see `WARN_WITHIN_SEC`.
    warn_within_sec: int = WARN_WITHIN_SEC

    #: The rail's own verdict -- the question the CLOCK does not answer. Rail 22's record can be
    #: REFUTED by venue evidence or attest a MARGIN-ENABLED account, both of which halt entries
    #: with the due date still months away.
    satisfied: bool = True

    @property
    def effective_warn_sec(self) -> int:
        """The window, never longer than the life of the attestation it guards.

        The 48-hour window is flat, so an attestation whose whole life is shorter than 48 hours
        would be `EXPIRING` on the instant it was made and the banner permanently lit. Capped at
        half the life, so there is always an `OK` half.
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


#: Every attestation a rail can veto on, with no dates filled in. `survey` returns these with the
#: deployment's own dates attached. Separate so a surface can describe the SET without a database
#: -- and so `tests/test_attestation_expiry.py` can assert the set is exactly this one, which is
#: what makes adding a rail an edit to a test that says why it was not covered.
_DEFINITIONS: tuple[Attestation, ...] = (
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

    A surface that already holds the dates needs the window, not another read of the same row.
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
        "cash_posture": lambda: _posture_dates(repo, venue),
    }
    # By KEY, never by index. `_DEFINITIONS[0]` was the first spelling and it fails two ways in
    # silence: a rail appended reaches `survey_definitions()` and never `survey()`, so it can
    # never be banner, doctor or ledger; and a REORDER attaches one rail's dates to another's
    # definition. A rail with no reader here surveys as `MISSING`, which is the safe answer
    # -- it says "nobody has attested" rather than "this is fine".
    return tuple(
        replace(definition, **readers[definition.key]())
        if definition.key in readers
        else definition
        for definition in _DEFINITIONS
    )


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
