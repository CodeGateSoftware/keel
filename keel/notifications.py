"""Deriving notification events from the state doctor already computes (#444).

The taxonomy, opt-in settings, formatting and transport live in `keel_core.notifications`.
This module is the BRIDGE: it turns a deployment's state into events, on two deliberately
separate seams:

* `events_from_state` is PURE. It takes doctor's own finding lists
  (`keel.commands.doctor.attestation_findings`/`rail_state_findings` outputs), the allowance
  numbers doctor's `allowance_findings` receives, and the cycle facts the agent loop already
  records. It re-implements NO threshold math -- rail 17's WARN-at-<=2-days and the rails'
  armed/healthy verdicts are read off doctor's findings, so the notification layer cannot
  drift from the surface an operator diagnoses with.

* `notify_after_cycle` is the wiring, run at the tail of every agent cycle (see
  `keel.agent.run_once`). It reads the SAME repo keys doctor's `gather_findings` reads,
  derives the events, and hands them to `keel_core.notifications.send_event`. It never
  raises. Nothing here is a control surface, and nothing here increases any capability --
  #436's TTY gates are untouched.

  It writes exactly ONE key, `NOTIFIED_WINDOWS_KEY`, added by #793 and the only departure from
  "notify-only, per #444's scope". Suppressing a repeated alert requires remembering what has
  already been said and there is nowhere else to remember it; the alternative was an
  `attestation.expiring` webhook on every cycle for as long as a rail stayed lapsed, which on
  the live deployment meant two of them, hourly, indefinitely. No rail, report or decision reads
  the key, so the worst a wrong value can do is send an alert twice or hold one back for one
  window. `tests/test_notifications.py` pins the write list as EXACTLY that one key.

The one threshold this module OWNS is `ALLOWANCE_NEARING_USED_PCT`: doctor's allowance
finding cannot express "nearing" (it WARNs only once the allowance is fully exhausted), and
the issue asks for the warning BEFORE that. The threshold is computed from the same
`(month_to_date_spend, allowance)` pair doctor receives, so there is still one source of
numbers.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from keel_core.alerting import resolve_webhook_url
from keel_core.notifications import (
    NotificationEvent,
    Transport,
    notification_event,
    send_event,
)
from keel_core.telemetry import current_venue, log_event

from keel import attestations
from keel.commands import doctor
from keel.config import Config
from keel.execution import guards

if TYPE_CHECKING:
    from keel.agent import LoopResult
    from keel.data.repository import Repository

#: Month-to-date BUY spend at (or past) this percent of the in-force rail-14 allowance fires
#: `allowance.nearing_exhaustion`. 80 leaves roughly a fifth of the month's cap -- enough
#: runway to act on the notification (re-tier or plan the rest of the month) rather than
#: learn about the rail the hard way, when it vetoes the next setup.
ALLOWANCE_NEARING_USED_PCT = Decimal("80")

#: The doctor findings rail-17's event reads. Rail 14's `attest.subscription` is deliberately
#: absent: a lapsed (or never-attested) subscription surfaces through the ALLOWANCE event
#: instead -- the unsubscribed allowance (0 by default) with month-to-date spend IS that
#: event's zero-runway case, so a rail-14 finding here would double-notify the same fact.
#: `attest.cash_posture` (#691) joins rail 17 here because it has the same failure shape and
#: a longer fuse: nothing re-confirms it, so it lapses on a clock, and the live profile runs
#: unattended. A doctor finding nobody is told about is only marginally better than the veto.
_ATTESTATION_FINDINGS = frozenset({"attest.withdrawals", "attest.cash_posture"})

#: Which rail each attestation finding belongs to, for the event message.
_RAIL_LABEL = {"attest.withdrawals": "rail 17", "attest.cash_posture": "rail 22"}

#: Each attestation finding's key in `keel.attestations`, which is where its expiry lives.
#: Two tables rather than one because they answer to different modules and a finding can be in
#: `_ATTESTATION_FINDINGS` without the model knowing it -- `test_every_finding_that_can_fire_the
#: _event_has_a_window` is the pin that says they must agree.
_ATTESTATION_MODEL_KEY = {
    "attest.withdrawals": "withdrawal",
    "attest.cash_posture": "cash_posture",
}

#: The repo key holding, per attestation finding, the window its last alert was sent for.
NOTIFIED_WINDOWS_KEY = "notified_attestation_windows"

#: The doctor findings the rail-armed event reads. `rail.kill_switch` is deliberately absent:
#: the kill switch is engaged by an operator at a TTY (doctor renders it "a correct state,
#: not a fault"), so the person it would notify already knows. The rails that arm THEMSELVES
#: from trading outcomes -- rail 16's streak halt, rail 11's drawdown breaker -- are the ones
#: worth a notification.
_ARMED_RAIL_FINDINGS = frozenset({"rail.streak_halt", "rail.drawdown"})

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnplacedSetup:
    """One entry setup the cycle DETECTED but did not place -- `signal` fired, the execution
    result came back `placed=False`. `reasons` carries the rails' violation names when it was
    vetoed, or the executor's refusal reason when it was not (a declined confirm, a paper
    skip)."""

    product: str
    rule: str
    reasons: tuple[str, ...]


def events_from_state(
    *,
    attestation_findings: Sequence[doctor.Finding],
    rail_findings: Sequence[doctor.Finding],
    month_to_date_spend: Decimal | None,
    allowance: Decimal | None,
    unplaced_setups: Sequence[UnplacedSetup],
    stale_products: Sequence[str],
    held_products: Sequence[str],
) -> list[NotificationEvent]:
    """Derive the #444 events from doctor's findings plus the cycle facts. Pure.

    Healthy state produces `[]`. Each unhealthy fact produces at most one event (staleness is
    per-product: one event per stale product WITH a position, because that is a per-position
    fact). Numbers ride in the message (doctor's own detail strings carry the days remaining)
    and in `fields` where they are structural (`pct_used`, `count`).
    """
    events: list[NotificationEvent] = []

    notified: set[str] = set()
    for finding in attestation_findings:
        if (
            finding.name in _ATTESTATION_FINDINGS
            and finding.name not in notified
            and finding.status in (doctor.WARN, doctor.FAIL)
        ):
            events.append(
                notification_event(
                    "attestation.expiring",
                    f"{_RAIL_LABEL[finding.name]}: {finding.headline} -- {finding.detail}",
                    finding=finding.name,
                    status=finding.status,
                    detail=finding.detail,
                )
            )
            # One event PER FINDING NAME, not one per cycle. This was a `break`, written when the
            # registry held rail 17 alone and the list genuinely carried one verdict. With rail 22
            # in it (#732) a break makes a cash-posture problem invisible whenever a withdrawals
            # problem also exists -- the same silence, one layer down. The names are unique across
            # the gatherers, so the guard below is about not repeating one, never about choosing
            # between two.
            notified.add(finding.name)

    for finding in rail_findings:
        if finding.name in _ARMED_RAIL_FINDINGS and finding.status != doctor.OK:
            events.append(
                notification_event(
                    "rail.armed",
                    f"{finding.headline} -- {finding.detail}",
                    rail=finding.name,
                    status=finding.status,
                    detail=finding.detail,
                )
            )

    if month_to_date_spend is not None and month_to_date_spend > 0 and allowance is not None:
        if allowance == 0:
            # Zero in-force allowance (the unsubscribed default) WITH month-to-date spend: a
            # lapsed or never-attested subscription that was active this month. That IS the
            # event's spirit -- zero runway left, the threshold passed in full -- so it fires
            # rather than being silenced by the `allowance > 0` the pct math needs.
            events.append(
                notification_event(
                    "allowance.nearing_exhaustion",
                    f"month-to-date BUY spend {month_to_date_spend} against an allowance of 0 "
                    f"-- no subscription is in force (lapsed or never attested); rail 14 "
                    f"vetoes further BUYs",
                    month_to_date_spend=str(month_to_date_spend),
                    allowance="0",
                    pct_used=str(Decimal("100")),
                )
            )
        else:
            pct_used = (month_to_date_spend * Decimal("100") / allowance).quantize(Decimal("0.01"))
            if pct_used >= ALLOWANCE_NEARING_USED_PCT:
                remaining = allowance - month_to_date_spend
                exhausted = remaining <= 0
                tail = "exhausted; the rail is vetoing BUYs" if exhausted else "nearing exhaustion"
                events.append(
                    notification_event(
                        "allowance.nearing_exhaustion",
                        f"month-to-date BUY spend {month_to_date_spend} of {allowance} "
                        f"({pct_used}% used) -- the monthly allowance is {tail}",
                        month_to_date_spend=str(month_to_date_spend),
                        allowance=str(allowance),
                        pct_used=str(pct_used),
                        remaining=str(remaining),
                    )
                )

    if unplaced_setups:
        shown = list(unplaced_setups)[:3]
        listed = "; ".join(
            f"{setup.product}/{setup.rule} ({', '.join(setup.reasons) or 'not placed'})"
            for setup in shown
        )
        extra = len(unplaced_setups) - len(shown)
        suffix = f" (+{extra} more)" if extra > 0 else ""
        events.append(
            notification_event(
                "setup.unplaced",
                f"{len(unplaced_setups)} detected setup(s) not placed this cycle: {listed}{suffix}",
                count=len(unplaced_setups),
                products=sorted({setup.product for setup in unplaced_setups}),
            )
        )

    for product in sorted(set(stale_products) & set(held_products)):
        events.append(
            notification_event(
                "feed.stale_open_position",
                f"feed for {product} is stale while a position is open -- the cycle skipped "
                f"it, so its rule-driven exits are riding on stopped data",
                product=product,
            )
        )

    return events


def notify_after_cycle(
    repo: Repository,
    config: Config,
    result: LoopResult,
    now_ts: int,
    *,
    url: str | None = None,
    transport: Transport | None = None,
) -> int:
    """Derive this cycle's events and deliver the opted-in ones. Returns the delivery count.

    Runs AFTER the cycle's trading work, at `run_once`'s tail, and can never break it: every
    failure -- an unreadable repo, a dead endpoint -- costs a notification, not a cycle. The one
    write it makes (the #793 alert ledger, see the module docstring) is inside that promise: it
    happens after delivery and its failure is logged and swallowed.
    Default-off short-circuits first (`notifications.events` empty means zero repo reads and
    zero network), and no configured URL (`KEEL_ALERT_WEBHOOK`, resolved via
    `keel_core.alerting.resolve_webhook_url`) means zero delivery attempts: the same
    offline-first contract the CRITICAL webhook keeps.

    The repo reads are exactly doctor's `gather_findings` reads -- one seam, so the
    notification and the diagnostic can never disagree about the state they describe.
    """
    try:
        settings = config.notifications
        if not settings.events:
            return 0
        resolved = url if url is not None else resolve_webhook_url()
        if not resolved:
            return 0

        venue = current_venue() or guards.DEFAULT_VENUE
        subscription = repo.get_broker_subscription(venue)
        attestation = [
            *doctor.attestation_findings(
                subscription=subscription,
                withdrawals_attested_at=int(
                    repo.get_state("withdrawals_attested_at", default=0) or 0
                ),
                withdrawals_enabled=repo.get_state("withdrawals_enabled", default=None),
                now_ts=now_ts,
            ),
            # #732. `attest.cash_posture` was in `_ATTESTATION_FINDINGS` and this call was not
            # here, so the registration was real and the delivery path was not: an operator who
            # wired a webhook for it would never have been told.
            #
            # It is the finding that fires when the account is attested MARGIN-ENABLED, when the
            # posture attestation has expired, or when it was attested with no due date at all --
            # three states in which rail 22 has stopped letting the agent enter positions. The
            # symptom otherwise is SILENCE: an agent that looks healthy and never trades again.
            *doctor.cash_posture_findings(
                repo.get_venue_cash_posture(venue), venue=venue, now_ts=now_ts
            ),
        ]
        rails = doctor.rail_state_findings(
            kill_switch=bool(repo.get_state("kill_switch", default=False)),
            streak_halt_until=int(repo.get_state("streak_halt_until", default=0) or 0),
            drawdown_total=Decimal(str(repo.get_state("drawdown_total_pct", default=0) or 0)),
            now_ts=now_ts,
        )

        events = events_from_state(
            attestation_findings=attestation,
            rail_findings=rails,
            month_to_date_spend=guards._monthly_buy_spend_usd(repo, now_ts),
            allowance=(
                subscription.allowance_usd(now_ts, Decimal("0"))
                if subscription is not None
                else None
            ),
            unplaced_setups=_unplaced_setups(result),
            stale_products=result.stale_products,
            held_products=repo.held_products(),
        )
        # #793: an attestation alert goes out ONCE for the window it reports. Applied here and
        # not in `events_from_state`, which stays pure: the ledger is a repo read, and the
        # suppression must key off what was actually DELIVERED, which only this loop knows.
        windows = attestation_windows(attestations.survey(repo, now_ts), now_ts)
        events = unreported(events, windows=windows, already=reported_windows(repo))

        sent = 0
        reported: dict[str, str] = {}
        for event in events:
            if send_event(resolved, event, settings, transport=transport):
                sent += 1
                window = windows.get(str(event.fields.get("finding", "")))
                if window is not None:
                    reported[str(event.fields["finding"])] = window
        # Recorded only for what left the building. A webhook that was down, or an event the
        # operator has not opted into, must not consume this window's one alert.
        record_reported(repo, reported)
        if events:
            log_event(
                _logger,
                logging.INFO,
                "notification.cycle",
                derived=len(events),
                sent=sent,
                keys=[event.key for event in events],
            )
        return sent
    except Exception:
        # Deliberately broad: see the docstring. A notification failure must never cost a
        # cycle, and the exception types an arbitrary repo/broker state can raise are not
        # enumerable here.
        log_event(_logger, logging.WARNING, "notification.cycle_failed")
        return 0


# -- one alert per window (#793) ---------------------------------------------------------------
#
# `attestation.expiring` fired on every cycle in which doctor's finding was WARN or FAIL. On the
# live deployment that is both rails at once, every cycle, for as long as they stay lapsed --
# and an alert that repeats until it is fixed stops being read on about the third day. The event
# is therefore sent once for the window it reports, and a window is the attestation's STATE
# paired with its EXPIRY: entering the final stretch alerts once, expiring alerts again (a halt
# is not a warning), renewing and lapsing again alerts again, and a rail that was never attested
# at all -- rail 22, today -- alerts once rather than forever.


def attestation_windows(surveyed: Sequence[Any], now_ts: int) -> dict[str, str]:
    """Each attestation finding's current window, keyed by the FINDING name the event carries.

    Keyed that way so the join to an event is the field the event already has (`finding`), not a
    second mapping applied at delivery time.
    """
    by_key = {attestation.key: attestation for attestation in surveyed}
    windows: dict[str, str] = {}
    for finding_name, model_key in _ATTESTATION_MODEL_KEY.items():
        attestation = by_key.get(model_key)
        if attestation is None:
            continue
        windows[finding_name] = f"{attestation.state(now_ts)}:{attestation.expires_at}"
    return windows


def unreported(
    events: Sequence[NotificationEvent],
    *,
    windows: Mapping[str, str],
    already: Mapping[str, str],
) -> list[NotificationEvent]:
    """`events` minus the attestation alerts already sent for the window they are in. Pure.

    **Only attestation alerts are ever dropped.** Every other event is a fact about the cycle
    that just ran -- an armed rail, an allowance, a stale product with a position -- and has no
    window to be inside. An event with no window in `windows` passes through untouched, so a
    finding added to `_ATTESTATION_FINDINGS` and forgotten here keeps alerting rather than
    falling silent: the failure that costs a duplicate is the one to prefer.
    """
    keep: list[NotificationEvent] = []
    for event in events:
        window = windows.get(str(event.fields.get("finding", "")))
        if window is not None and already.get(str(event.fields.get("finding"))) == window:
            continue
        keep.append(event)
    return keep


def reported_windows(repo: Repository) -> dict[str, str]:
    """The ledger, or `{}` when it cannot be read.

    **Fails OPEN.** An unreadable or corrupt key means an alert is sent that may be a duplicate;
    the other reading of the same failure withholds the one alert that says live is halted.
    """
    try:
        raw = repo.get_state(NOTIFIED_WINDOWS_KEY, default=None)
        if raw is None:
            return {}
        loaded = raw if isinstance(raw, dict) else json.loads(str(raw))
        return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def record_reported(repo: Repository, sent: Mapping[str, str]) -> None:
    """Merge the windows just alerted on into the ledger. Never raises.

    **This module writes**, which `notify_after_cycle`'s docstring said it never did, and the
    departure is deliberate rather than overlooked: suppressing a repeat requires remembering
    what was already said, and there is nowhere else to remember it. The write is one state key
    that no rail, report or decision reads -- nothing about what keel TRADES can turn on it, and
    the worst a wrong value can do is send an alert twice or hold one back for one window.
    """
    if not sent:
        return
    try:
        merged = {**reported_windows(repo), **{str(k): str(v) for k, v in sent.items()}}
        repo.set_state(NOTIFIED_WINDOWS_KEY, json.dumps(merged, sort_keys=True))
    except Exception:  # pragma: no cover - a ledger that cannot be written costs a duplicate
        log_event(_logger, logging.WARNING, "notification.ledger_write_failed")


def _unplaced_setups(result: LoopResult) -> tuple[UnplacedSetup, ...]:
    """The cycle's detected-but-not-placed entries, from the loop's parallel signal/result
    lists (`run_once` appends them in lockstep)."""

    def _reasons(execution: Any) -> tuple[str, ...]:
        if execution.vetoed_by:
            return tuple(execution.vetoed_by)
        return (str(execution.reason),) if execution.reason else ()

    return tuple(
        UnplacedSetup(
            product=signal.product_id,
            rule=signal.rule_name,
            reasons=_reasons(execution),
        )
        for signal, execution in zip(result.enter_signals, result.enter_results)
        if not execution.placed
    )
