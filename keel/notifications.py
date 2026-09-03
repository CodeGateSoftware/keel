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
  raises and never writes: notify-only, per #444's scope. Nothing here is a control surface,
  and nothing here increases any capability -- #436's TTY gates are untouched.

The one threshold this module OWNS is `ALLOWANCE_NEARING_USED_PCT`: doctor's allowance
finding cannot express "nearing" (it WARNs only once the allowance is fully exhausted), and
the issue asks for the warning BEFORE that. The threshold is computed from the same
`(month_to_date_spend, allowance)` pair doctor receives, so there is still one source of
numbers.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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

    for finding in attestation_findings:
        if finding.name in _ATTESTATION_FINDINGS and finding.status in (doctor.WARN, doctor.FAIL):
            events.append(
                notification_event(
                    "attestation.expiring",
                    f"{_RAIL_LABEL[finding.name]}: {finding.headline} -- {finding.detail}",
                    finding=finding.name,
                    status=finding.status,
                    detail=finding.detail,
                )
            )
            break  # one event per cycle: the finding list carries one rail-17 verdict

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
    failure -- an unreadable repo, a dead endpoint -- costs a notification, not a cycle.
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
        attestation = doctor.attestation_findings(
            subscription=subscription,
            withdrawals_attested_at=int(repo.get_state("withdrawals_attested_at", default=0) or 0),
            now_ts=now_ts,
        )
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
        sent = 0
        for event in events:
            if send_event(resolved, event, settings, transport=transport):
                sent += 1
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
