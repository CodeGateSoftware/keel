"""keel doctor (#443) — one command that answers 'is this deployment actually working'.

`keel status` reports what a deployment is DOING; doctor reports whether it actually
CAN. The motivating case: a paper profile vetoed 15 of 15 detected setups on
`subscription_unattested` for weeks while every status line looked healthy, and the
diagnosis meant hand-parsing the JSONL log and recomputing sizing by hand.

Two disciplines make this worth running:

* every finding names the COMMAND that fixes it -- the value is the next step, not
  the diagnosis;
* `halted` is a first-class status beside ok/warn/fail. An armed kill-switch or an
  unexpired streak halt is a CORRECT state, deliberately entered, and must not fail
  the run. Only genuine faults do.

The checks are pure functions over plain values and log lines, so they are testable
without a database; the thin click command at the bottom is the only place that
touches the repo, the config, and the log file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import click
from keel_core.telemetry import current_venue

from keel.version import build_info, check_install

#: Rail 17's TTL is the executor's constant; doctor only READS it (7 days).
TTL_SEC = 7 * 86_400

OK = "ok"
WARN = "warn"
FAIL = "fail"
HALTED = "halted"


@dataclass(frozen=True)
class Finding:
    """One doctor verdict. `fix` names the command that resolves it ('-' when none
    is needed); `detail` carries the numbers (days remaining, dollars, counts)."""

    name: str
    status: str
    headline: str
    detail: str
    fix: str


def _days(seconds: float) -> int:
    return int(max(seconds, 0) // 86_400)


def attestation_findings(
    subscription: Any | None,
    withdrawals_attested_at: int,
    now_ts: int,
    ttl_sec: int = TTL_SEC,
) -> list[Finding]:
    """Rails 14 and 17 -- days remaining, not just valid/invalid."""
    findings: list[Finding] = []

    if subscription is None:
        findings.append(
            Finding(
                "attest.subscription",
                FAIL,
                "no subscription attested",
                "rail 14 falls back to the unsubscribed allowance; every BUY must fit it",
                "keel subscription attest --venue <venue> --tier <tier>",
            )
        )
    else:
        due = int(subscription.attest_due_ts)
        remaining = _days(due - now_ts)
        if remaining <= 0:
            findings.append(
                Finding(
                    "attest.subscription",
                    WARN,
                    "attestation overdue",
                    f"due {abs(_days(now_ts - due))} day(s) ago; the rail reads it as stale",
                    "keel subscription attest --venue <venue> --tier <tier>",
                )
            )
        elif remaining <= 2:
            findings.append(
                Finding(
                    "attest.subscription",
                    WARN,
                    "attestation due soon",
                    f"{remaining} day(s) of freshness remain",
                    "keel subscription attest --venue <venue> --tier <tier>",
                )
            )
        else:
            findings.append(
                Finding(
                    "attest.subscription",
                    OK,
                    "subscription attested",
                    f"{remaining} day(s) of freshness remain",
                    "-",
                )
            )

    if withdrawals_attested_at <= 0:
        findings.append(
            Finding(
                "attest.withdrawals",
                FAIL,
                "withdrawal capability never attested",
                "rail 17 (qabd) halts every BUY entry until it is",
                "keel withdrawals attest --enabled",
            )
        )
    else:
        age = now_ts - withdrawals_attested_at
        remaining = _days(ttl_sec - age)
        if remaining <= 0:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    FAIL,
                    "withdrawal attestation expired",
                    f"expired {abs(_days(age - ttl_sec))} day(s) ago; rail 17 halts entries",
                    "keel withdrawals attest --enabled",
                )
            )
        elif remaining <= 2:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    WARN,
                    "withdrawal attestation due soon",
                    f"{remaining} day(s) remain on the {ttl_sec // 86_400}-day TTL",
                    "keel withdrawals attest --enabled",
                )
            )
        else:
            findings.append(
                Finding(
                    "attest.withdrawals",
                    OK,
                    "withdrawal capability attested",
                    f"{remaining} day(s) remain on the {ttl_sec // 86_400}-day TTL",
                    "-",
                )
            )
    return findings


def rail_state_findings(
    kill_switch: bool,
    streak_halt_until: int,
    drawdown_total: Decimal,
    now_ts: int,
    total_threshold: Decimal = Decimal("20"),
) -> list[Finding]:
    """Armed halts, reported as the deliberate states they are."""
    findings: list[Finding] = []
    if kill_switch:
        findings.append(
            Finding(
                "rail.kill_switch",
                HALTED,
                "kill switch engaged",
                "every entry is vetoed; this is a correct state, not a fault",
                "keel autonomy on",
            )
        )
    else:
        findings.append(
            Finding("rail.kill_switch", OK, "kill switch clear", "entries may proceed", "-")
        )

    if streak_halt_until > now_ts:
        hours = (streak_halt_until - now_ts) / 3600
        findings.append(
            Finding(
                "rail.streak_halt",
                HALTED,
                "consecutive-loss halt armed",
                f"clears in {hours:.0f}h; it expires on its own",
                "wait for the window to pass (rail 16 clears itself)",
            )
        )
    else:
        findings.append(Finding("rail.streak_halt", OK, "no streak halt", "-", "-"))

    if drawdown_total >= total_threshold:
        findings.append(
            Finding(
                "rail.drawdown",
                FAIL,
                "total drawdown at or past the 20% rail",
                f"{drawdown_total}% -- rail 11 is vetoing entries at this level",
                "see the operator runbook: drawdown recovery procedure",
            )
        )
    else:
        findings.append(
            Finding(
                "rail.drawdown",
                OK,
                "drawdown inside the rail",
                f"{drawdown_total}% of the 20% total",
                "-",
            )
        )
    return findings


def allowance_findings(
    month_to_date_spend: Decimal,
    allowance: Decimal | None,
    mean_buy_notional: Decimal | None,
) -> list[Finding]:
    """Rail 14 headroom: what remains, and how many typical orders that is."""
    if allowance is None:
        return [
            Finding(
                "allowance.headroom",
                OK,
                "unlimited allowance in force",
                f"allowance unlimited; month-to-date BUY spend {month_to_date_spend}",
                "-",
            )
        ]
    remaining = allowance - month_to_date_spend
    if remaining <= 0:
        return [
            Finding(
                "allowance.headroom",
                WARN,
                "allowance exhausted",
                f"month-to-date BUY spend {month_to_date_spend} of {allowance}; the rail "
                "vetoes further BUYs until the month rolls over",
                "keel subscription show  (then wait for rollover or attest a higher tier)",
            )
        ]
    detail = f"{remaining} of {allowance} remains (spent {month_to_date_spend})"
    if mean_buy_notional and mean_buy_notional > 0:
        typical = (remaining / mean_buy_notional).quantize(Decimal("1"), ROUND_HALF_UP)
        detail += f" -- about {typical} typical order(s)"
    return [Finding("allowance.headroom", OK, "allowance headroom", detail, "-")]


def veto_findings(lines: Iterable[str], since_ts: float) -> list[Finding]:
    """The motivating case, made impossible to miss: aggregate `executor.order_vetoed`
    events since `since_ts` and name the dominant reason's fix. One reason vetoing
    everything is a FAIL; a spread of reasons is a WARN with the top three."""
    counts: dict[str, int] = {}
    total = 0
    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("event") != "executor.order_vetoed":
            continue
        if float(event.get("ts", 0)) < since_ts:
            continue
        total += 1
        for violation in event.get("violations", []):
            reason = str(violation).split(":", 1)[0].strip()
            counts[reason] = counts.get(reason, 0) + 1

    if total == 0:
        return [
            Finding(
                "veto.recent",
                OK,
                "no recent vetoes",
                "no executor.order_vetoed events in the window",
                "-",
            )
        ]

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top_reason, top_count = ranked[0]
    top_line = ", ".join(f"{reason} x{count}" for reason, count in ranked[:3])
    if top_count >= total:  # one reason vetoed EVERY entry in the window
        return [
            Finding(
                "veto.recent",
                FAIL,
                f"every entry vetoed by {top_reason}",
                f"{top_count} of {total} vetoed entries, all on {top_reason} -- this is "
                f"the pattern to catch ({top_line})",
                _fix_for_reason(top_reason),
            )
        ]
    return [
        Finding(
            "veto.recent",
            WARN,
            f"{total} vetoed entries in the window",
            f"top reasons: {top_line}",
            _fix_for_reason(top_reason),
        )
    ]


def _fix_for_reason(reason: str) -> str:
    if "subscription" in reason or "unattested" in reason:
        return "keel subscription attest --venue <venue> --tier <tier>"
    if "allowance" in reason:
        return "keel subscription show  (headroom vs the month's cap)"
    if "kill" in reason or "drawdown" in reason or "streak" in reason:
        return "see the operator runbook: the halt and how it clears"
    return "keel status  (then the operator runbook for the failing rail)"


def doctor_exit_code(findings: list[Finding]) -> int:
    """Faults fail the run; deliberate halts and warnings do not."""
    return 1 if any(f.status == FAIL for f in findings) else 0


_STATUS_MARK = {OK: "[ok]", WARN: "[warn]", FAIL: "[FAIL]", HALTED: "[halted]"}


def doctor_lines(findings: list[Finding]) -> list[str]:
    lines = ["keel doctor -- is this deployment actually working?", ""]
    for f in findings:
        lines.append(f"{_STATUS_MARK.get(f.status, '[?]')} {f.name}: {f.headline}")
        lines.append(f"       {f.detail}")
        if f.fix != "-":
            lines.append(f"       fix: {f.fix}")
    counts = {s: sum(1 for f in findings if f.status == s) for s in (OK, WARN, FAIL, HALTED)}
    lines.append("")
    lines.append(
        f"{counts[OK]} ok, {counts[WARN]} warn, {counts[FAIL]} fail, {counts[HALTED]} halted "
        "(halted = deliberate, not broken)"
    )
    return lines


def render_json(findings: list[Finding]) -> str:
    return json.dumps(
        [
            {
                "name": f.name,
                "status": f.status,
                "headline": f.headline,
                "detail": f.detail,
                "fix": f.fix,
            }
            for f in findings
        ],
        indent=2,
    )


def _read_log_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:  # noqa: SIM115
            return handle.readlines()
    except OSError:
        return []


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="emit findings as JSON")
@click.option("--log", "log_path", default="logs/keel.log", show_default=True)
@click.pass_context
def doctor_cmd(ctx: click.Context, as_json: bool, log_path: str) -> None:
    """One command that answers 'is this deployment actually working' (#443)."""
    from keel.commands._common import _load_cfg, _open_repo
    from keel.execution import guards

    try:
        config = _load_cfg(ctx)
        repo = _open_repo(ctx)
    except click.ClickException:
        startup: list[Finding] = [
            Finding(
                "install.identity",
                FAIL,
                "cannot load config/database",
                "doctor needs the deployment's config and repo before any check can run",
                "keel init  (fresh) or check the --config/--db path",
            )
        ]
        _emit(startup, as_json)
        raise SystemExit(1)

    now_ts = int(__import__("time").time())
    findings: list[Finding] = []

    info = build_info()
    report = check_install(source=info.source)
    versions_aligned = getattr(report, "consistent", True)
    identity = getattr(report, "identity", "")
    if versions_aligned and "DIRTY" not in str(identity) and "[checkout]" not in str(identity):
        findings.append(Finding("install.identity", OK, "build identity clean", str(identity), "-"))
    else:
        findings.append(
            Finding(
                "install.identity",
                FAIL,
                "install is skewed or not a release build",
                f"{identity}; a skewed install can run older siblings silently",
                "reinstall keel_trader BY PATH from the release; verify with `keel versions`",
            )
        )

    venue = current_venue() or guards.DEFAULT_VENUE
    subscription = repo.get_broker_subscription(venue)
    findings += attestation_findings(
        subscription=subscription,
        withdrawals_attested_at=int(repo.get_state("withdrawals_attested_at", default=0) or 0),
        now_ts=now_ts,
    )
    findings += rail_state_findings(
        kill_switch=bool(repo.get_state("kill_switch", default=False)),
        streak_halt_until=int(repo.get_state("streak_halt_until", default=0) or 0),
        drawdown_total=Decimal(str(repo.get_state("drawdown_total_pct", default=0) or 0)),
        now_ts=now_ts,
    )
    findings += allowance_findings(
        month_to_date_spend=guards._monthly_buy_spend_usd(repo, now_ts),
        allowance=(
            subscription.allowance_usd(now_ts, Decimal("0")) if subscription is not None else None
        ),
        mean_buy_notional=None,
    )
    findings += veto_findings(_read_log_lines(log_path), since_ts=now_ts - 7 * 86_400)

    _emit(findings, as_json)
    _ = config  # checks read the repo and the build, not the config, in this slice
    raise SystemExit(doctor_exit_code(findings))


def _emit(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        click.echo(render_json(findings))
    else:
        for line in doctor_lines(findings):
            click.echo(line)
