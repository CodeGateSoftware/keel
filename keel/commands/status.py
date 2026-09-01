"""`keel status` -- the interim, read-only operator dashboard for a paper-forward.

The paper-mode-fidelity spec explicitly deferred a dedicated status command as a follow-up
("A dedicated `keel status` command is deferred as a follow-up"); this is that follow-up. Its
job is narrow: let an operator running a paper-forward see the agent's state at a glance,
**purely from the local DB and config** -- mode, kill-switch, autonomy, Rail 11 drawdown/equity
state, rail 17's withdrawal-attestation freshness, open positions, rule counts, and per-product
data freshness. It NEVER calls the broker; that is the whole point (`monitor`/`agent` are the
commands that touch the network).

Two layers, matching the rest of `keel/commands/*`:

- `gather_status` is a PURE function of `(Repository, Config, now_ts)` -> `StatusReport`. It does
  no I/O beyond the repo/config it is handed and takes no click dependency, so it is directly
  unit-testable without a CLI runner or a broker.
- `status_cmd` (registered in `keel/cli.py` as `keel status`) opens the repo/config via the
  standard `_open_repo`/`_load_cfg` seams, calls `gather_status`, and renders it -- either as
  `click.echo` lines (default) or as JSON (`--json`), which exists mainly as a stable,
  machine-readable shape for the eventual TUI to consume without re-deriving this logic.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

import click

from keel import agent
from keel.commands._common import DISCLAIMER, _load_cfg, _open_repo
from keel.commands._products import _default_sim_products
from keel.config import Config
from keel.data.repository import Repository
from keel.execution import executor
from keel.types import Granularity

# -- the pure report shape ---------------------------------------------------------------------


@dataclass(frozen=True)
class AutonomyStatus:
    """Mirrors `keel autonomy show`'s own reading, so the two commands never disagree."""

    live: bool
    autonomous: bool
    autonomous_until: int | None
    updated_ts: int | None
    profile_readable: bool


@dataclass(frozen=True)
class OpenPositionStatus:
    id: int
    product_id: str
    rule_name: str
    qty: Decimal
    entry_price: Decimal
    opened_at: int
    has_bracket: bool
    #: The stop this tranche was SIZED against (`positions.initial_stop`, #520), when the row
    #: recorded one. `None` for a tranche that predates the v12 migration or a DCA leg that never
    #: got one (`repository._position_row_to_dict`'s own note) -- NOT "no stop", just "not on this
    #: row". Carried here so a paper position's bracket column (#641) can name the protection
    #: `PaperTrader.on_candle` actually enforces instead of a venue order paper never places.
    #: Defaulted so every existing `OpenPositionStatus(...)` construction stays valid -- the same
    #: pattern `StatusReport.market_session` uses for its own later-added field.
    initial_stop: Decimal | None = None


@dataclass(frozen=True)
class RuleSummary:
    id: int
    kind: str
    status: str
    product_id: str | None
    params: dict[str, Any]


@dataclass(frozen=True)
class ProductFreshness:
    product_id: str
    granularity: str | None
    last_ts: int | None
    age_sec: int | None


@dataclass(frozen=True)
class SubscriptionStatusRow:
    venue: str
    tier_name: str
    pacing: str
    stored_status: str
    effective_status: str
    effective_cap: Decimal | None


@dataclass(frozen=True)
class WithdrawalAttestationStatus:
    """Rail 17's input, as a displayable state (§65.4) -- the same reading `keel withdrawals
    show` prints, so the two commands can never disagree about whether entries are halted.

    `expires_in_sec` is set only when `state == "attested"` (a fresh `--enabled` attestation);
    `expired_for_sec` only when `state == "expired"` -- how long ago the 7-day TTL lapsed, not
    how old the attestation is, because "unknown for 12 days" is the fact that matters to an
    operator deciding how urgently to re-attest.
    """

    state: str  # "attested" | "suspended" | "expired" | "unattested"
    enabled: bool | None
    attested_at: int | None
    expires_in_sec: int | None
    expired_for_sec: int | None


@dataclass(frozen=True)
class MarketSessionStatus:
    """The venue's session state as the last agent cycle recorded it (FR-9) -- the #345
    rail-17 pattern applied to the market clock: name the state that gates the cycle, on its
    own line, before an operator has to wonder why nothing trades.

    Read from `agent_state` (`agent.market_session_key`), never from a broker call: this
    dashboard's whole design is "local DB + config only". `state is None` means no session-
    bound venue has recorded anything -- a 24/7 deployment, or one whose agent has not run --
    and renders NO line, so crypto dashboards stay byte-identical.

    `defused` says whether that recorded CLOSED still vouches for the quiet: closed AND
    inside its trust window (`agent.recorded_market_closed`) is the state under which
    staleness does not alert, and the TUI's freshness styling keys off it so the cells and
    the session line can never disagree about the same weekend.
    """

    state: str | None  # "open" | "closed" | "clock_unavailable" | None (not session-bound)
    recorded_ts: int | None
    #: Defaulted so every existing `MarketSessionStatus(...)` construction (the TUI tests)
    #: stays valid -- the same pattern `StatusReport` itself uses for this very field.
    defused: bool = False


@dataclass(frozen=True)
class StatusReport:
    now_ts: int
    mode: str
    kill_switch_engaged: bool
    autonomy: AutonomyStatus
    equity_state_mode: str | None
    high_water_mark: Decimal | None
    drawdown_total_pct: Decimal | None
    drawdown_weekly_pct: Decimal | None
    max_total_dd_pct: Decimal
    max_weekly_dd_pct: Decimal
    rail11_status: str
    withdrawal_attestation: WithdrawalAttestationStatus
    paper_cash_usdc: Decimal | None
    open_positions: list[OpenPositionStatus]
    rule_counts: dict[str, int]
    live_rules: list[RuleSummary]
    data_freshness: list[ProductFreshness]
    subscriptions: list[SubscriptionStatusRow]
    #: Defaulted to "no session recorded" so every existing `StatusReport(...)` construction
    #: (the TUI tests' `_base_report`, any external caller) stays valid -- the same pattern
    #: `LoopResult` uses for its later-added fields. Last for the same reason: a defaulted
    #: field anywhere earlier would force a default on every field after it.
    market_session: MarketSessionStatus = MarketSessionStatus(state=None, recorded_ts=None)


# -- gather (pure) ------------------------------------------------------------------------------


def _rail11_status(
    dd_total: Decimal | None,
    dd_weekly: Decimal | None,
    max_total: Decimal,
    max_weekly: Decimal,
) -> str:
    """"HALTED" if either drawdown is at/over its ceiling (matches `execution.guards` rail 11's
    own `>=` comparison), "unknown" if either scalar was never written, else "ok".

    Guarding on `None` matters here in a way it does not in `guards.py`: the guard reads
    `get_state(..., default=Decimal("0"))` because it must make a PASS/VETO decision every
    cycle and "no data yet" has to fail safe as "no drawdown". This is a DISPLAY, not a veto --
    reporting an unwritten value as a confident "ok" would be a lie (there may be a real
    breach the agent just hasn't computed yet), so it is surfaced as "unknown" instead.
    """
    if dd_total is None or dd_weekly is None:
        return "unknown"
    if dd_total >= max_total or dd_weekly >= max_weekly:
        return "HALTED"
    return "ok"


def _finest_granularity(granularities: list[Granularity]) -> Granularity | None:
    """The shortest-timeframe granularity configured, by `Granularity`'s own declaration order
    (`ONE_MINUTE` ... `ONE_DAY`, finest first) -- freshness is most informative measured against
    whichever series updates most often."""
    if not granularities:
        return None
    order = {g: i for i, g in enumerate(Granularity)}
    return min(granularities, key=lambda g: order[g])


def _autonomy_status(repo: Repository, now_ts: int) -> AutonomyStatus:
    profile = repo.get_profile()
    return AutonomyStatus(
        live=profile.is_autonomous(now_ts),
        autonomous=profile.autonomous,
        autonomous_until=profile.autonomous_until,
        updated_ts=profile.updated_ts,
        profile_readable=repo.profile_readable(),
    )


def _open_position_status(row: dict[str, Any]) -> OpenPositionStatus:
    return OpenPositionStatus(
        id=row["id"],
        product_id=row["product_id"],
        rule_name=row["rule_name"],
        qty=row["qty"],
        entry_price=row["entry_fill"],
        opened_at=row["opened_at"],
        has_bracket=row["bracket_order_id"] is not None,
        initial_stop=row["initial_stop"],
    )


def _rule_summary(row: dict[str, Any]) -> RuleSummary:
    params = row.get("params") or {}
    return RuleSummary(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        product_id=params.get("product_id"),
        params=params,
    )


def _data_freshness(repo: Repository, config: Config, now_ts: int) -> list[ProductFreshness]:
    granularity = _finest_granularity(list(config.market_data.granularities))
    rows = []
    for product_id in _default_sim_products(config):
        if granularity is None:
            rows.append(ProductFreshness(product_id, None, None, None))
            continue
        candles = repo.get_candles(product_id, granularity)
        if not candles:
            rows.append(ProductFreshness(product_id, granularity.value, None, None))
            continue
        last_ts = candles[-1].ts
        rows.append(
            ProductFreshness(product_id, granularity.value, last_ts, max(now_ts - last_ts, 0))
        )
    return rows


def _subscription_rows(
    repo: Repository, config: Config, now_ts: int
) -> list[SubscriptionStatusRow]:
    unsubscribed = config.subscription.unsubscribed_allowance_usd
    return [
        SubscriptionStatusRow(
            venue=record.venue,
            tier_name=record.tier_name,
            pacing=record.pacing,
            stored_status=record.status.value,
            effective_status=record.effective_status(now_ts).value,
            effective_cap=record.allowance_usd(now_ts, unsubscribed),
        )
        for record in repo.list_broker_subscriptions()
    ]


def _market_session(repo: Repository, config: Config, now_ts: int) -> MarketSessionStatus:
    """The venue's recorded session answer, read through `agent`'s own state keys -- the same
    discipline `_withdrawal_attestation` keeps toward the executor: this display reads what
    the engine wrote, never a re-derivation that could disagree with the cycle's decision.

    The records are venue-namespaced (`market_session:{venue}`), and this dashboard holds no
    broker to ask which venue it serves, so `agent.recorded_session_venues` discovers the
    recorded slots and the most recently stamped one is displayed -- a deployment has one
    venue, so there is exactly one slot in every supported topology. `defused` comes from
    `agent.recorded_market_closed` (closed AND inside the trust window), which is the same
    read `fetch --check` defuses on -- one source of truth for every rendering of the quiet.
    """
    best: tuple[int, str, int | None] | None = None
    for venue in agent.recorded_session_venues(repo):
        state = repo.get_state(agent.market_session_key(venue))
        if state is None:
            continue
        recorded_ts = repo.get_state(agent.market_session_ts_key(venue))
        stamped = recorded_ts if isinstance(recorded_ts, int) else -1
        if best is None or stamped > best[0]:
            best = (stamped, str(state), stamped if stamped > 0 else None)
    if best is None:
        return MarketSessionStatus(state=None, recorded_ts=None)
    return MarketSessionStatus(
        state=best[1],
        recorded_ts=best[2],
        defused=agent.recorded_market_closed(repo, config, now_ts),
    )


def _withdrawal_attestation(repo: Repository, now_ts: int) -> WithdrawalAttestationStatus:
    """Rail 17's input, resolved and aged for display.

    Resolution goes through the executor's OWN `_withdrawals_enabled` and expiry math goes
    through the executor's OWN `WITHDRAWAL_ATTESTATION_TTL_SEC` -- never a restated 7 days --
    so this display can never call an attestation fresh on the very cycle the rail vetoes it.
    Staleness takes precedence over suspension, matching `withdrawals show`: a stale attested
    suspension is UNKNOWN, not a fresh claim that withdrawals are down.

    The corrupt-state read is guarded the way the executor guards its own (`try/except` ->
    unknown): a dashboard must not inherit a crash path the rail deliberately does not have.
    """
    resolved = executor._withdrawals_enabled(repo, now_ts)
    try:
        attested_at = int(repo.get_state("withdrawals_attested_at", default=0) or 0)
    except (TypeError, ValueError):
        attested_at = 0

    if not attested_at:
        return WithdrawalAttestationStatus("unattested", None, None, None, None)
    enabled_flag = repo.get_state("withdrawals_enabled", default=None)
    if resolved is None and enabled_flag is None:
        # `withdrawals show`'s UNKNOWN corner, kept word-for-word: an attested_at exists but
        # the enabled flag itself is unreadable/absent, which only DB surgery produces (the
        # CLI writes both keys). Not "expired" -- the staleness question was never reached.
        return WithdrawalAttestationStatus("unknown", None, attested_at, None, None)
    if resolved is None:
        expired_for = max((now_ts - attested_at) - executor.WITHDRAWAL_ATTESTATION_TTL_SEC, 0)
        return WithdrawalAttestationStatus(
            "expired",
            enabled_flag,
            attested_at,
            None,
            expired_for,
        )
    if resolved is False:
        return WithdrawalAttestationStatus("suspended", False, attested_at, None, None)
    expires_in = max((attested_at + executor.WITHDRAWAL_ATTESTATION_TTL_SEC) - now_ts, 0)
    return WithdrawalAttestationStatus("attested", True, attested_at, expires_in, None)


def gather_status(repo: Repository, config: Config, now_ts: int) -> StatusReport:
    """Assemble the full status report from `repo`/`config` alone -- no broker, no network.

    Pure aside from the read-only `repo` calls it makes: given the same DB contents and config it
    always returns the same report, which is what makes it directly unit-testable and safe to
    call from both the human-readable and `--json` renderers without divergence.
    """
    dd_total = repo.get_state("drawdown_total_pct")
    dd_weekly = repo.get_state("drawdown_weekly_pct")
    max_total = config.money_mgmt.max_total_dd_pct
    max_weekly = config.money_mgmt.max_weekly_dd_pct

    rules = repo.get_rules()
    rule_counts: dict[str, int] = {}
    for row in rules:
        rule_counts[row["status"]] = rule_counts.get(row["status"], 0) + 1
    live_rules = [_rule_summary(row) for row in rules if row["status"] == "live"]

    return StatusReport(
        now_ts=now_ts,
        mode=config.auto_trade.mode,
        kill_switch_engaged=bool(repo.get_state("kill_switch", default=True)),
        autonomy=_autonomy_status(repo, now_ts),
        equity_state_mode=repo.get_state("equity_state_mode"),
        high_water_mark=repo.get_state("equity_high_water_mark"),
        drawdown_total_pct=dd_total,
        drawdown_weekly_pct=dd_weekly,
        max_total_dd_pct=max_total,
        max_weekly_dd_pct=max_weekly,
        rail11_status=_rail11_status(dd_total, dd_weekly, max_total, max_weekly),
        withdrawal_attestation=_withdrawal_attestation(repo, now_ts),
        paper_cash_usdc=(
            repo.get_state("paper_cash_usdc") if config.auto_trade.mode == "paper" else None
        ),
        open_positions=[_open_position_status(row) for row in repo.get_open_positions()],
        rule_counts=rule_counts,
        live_rules=live_rules,
        data_freshness=_data_freshness(repo, config, now_ts),
        subscriptions=_subscription_rows(repo, config, now_ts),
        market_session=_market_session(repo, config, now_ts),
    )


# -- render (human-readable) --------------------------------------------------------------------


def _human_age(age_sec: int) -> str:
    if age_sec < 60:
        return f"{age_sec}s ago"
    minutes = age_sec // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = age_sec // 3600
    if hours < 24:
        return f"{hours}h ago"
    days = age_sec // 86400
    return f"{days}d ago"


def _human_remaining(remaining_sec: int) -> str:
    """`_human_age`'s mirror image, for time still left rather than time already gone -- the
    same ladder (s/m/h/d) so the two read as one convention on adjacent lines."""
    if remaining_sec < 60:
        return f"{remaining_sec}s"
    minutes = remaining_sec // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = remaining_sec // 3600
    if hours < 24:
        return f"{hours}h"
    days = remaining_sec // 86400
    return f"{days}d"


def _rail17_line(w: WithdrawalAttestationStatus, rail_evaluated: bool) -> str:
    """The rail-17 line, naming the halt and the release in the same breath whenever entries
    are halted -- the 2026-08-14 event was invisible until the veto fired precisely because
    nothing surfaced the attestation's age BEFORE rail 17 acted on it (#340).

    `rail_evaluated` is False in paper mode: rail 17 is a LIVE_STATE rail, skipped offline,
    so a stale attestation halts nothing there. Claiming "entries halted" on a paper
    dashboard would be a permanently-red alert for a halt that cannot occur -- the exact
    alert-fatigue failure #340 exists to fix -- so the paper rendering names the state and
    says the rail is not evaluated, and the re-attest prompt is kept (the LIVE deployment's
    attestation is refreshed by the same typed command, and paper status should still nudge).
    """
    prefix = "rail 17 (withdrawal capability)"
    if w.state == "attested":
        return f"{prefix}: attested, expires in {_human_remaining(w.expires_in_sec or 0)}"
    if w.state == "unknown":
        return f"{prefix}: UNKNOWN (state unreadable); re-attest with keel withdrawals attest"
    halt = " -- entries halted" if rail_evaluated else " (rail 17 not evaluated in paper)"
    if w.state == "suspended":
        return (
            f"{prefix}: SUSPENDED{halt}; "
            "re-attest with keel withdrawals attest --enabled"
        )
    if w.state == "expired":
        return (
            f"{prefix}: EXPIRED {_human_age(w.expired_for_sec or 0)}{halt}; "
            "re-attest with keel withdrawals attest"
        )
    return f"{prefix}: never attested{halt}; re-attest with keel withdrawals attest"


def _session_line(s: MarketSessionStatus) -> str | None:
    """The market-session line (FR-9), or `None` when there is nothing to say.

    Rendered directly under the kill-switch line: both answer "why is the cycle not
    trading". Unlike rail 17 there is deliberately NO paper-mode carve-out -- the session
    gate skips PAPER cycles too (`keel.agent.run_once` checks the clock before the mode is
    even resolved), so the same line is truthful in every mode.

    `closed` names BOTH the skip and the alert relief in one breath: on a weekend the
    question this line exists to answer is "is it dead, or is it the weekend?", and an answer
    that only said "closed" would leave the staleness silence half-explained.
    """
    if s.state == "open":
        return "market session: open (venue clock)"
    if s.state == "closed":
        return "market session: CLOSED (venue clock) -- cycles skip, staleness does not alert"
    if s.state == "clock_unavailable":
        return (
            "market session: clock UNREADABLE (fail-closed) -- cycles skip until the "
            "venue clock answers"
        )
    return None


def _bracket_display(pos: OpenPositionStatus, mode: str) -> str:
    """The bracket column's text for one open position (#641).

    Live's meaning is untouched and load-bearing: `NO bracket` there names a real venue-order
    gap -- exactly the state `reconcile_unbracketed_positions` and the `unbracketed:` crash
    ledger (#519, #502) exist to heal. Paper never places a venue order, so `has_bracket` is
    False on every paper position by construction; rendering that as the SAME WARN reports the
    mode, not a hazard, and trains the reader to ignore the live case that matters.

    Paper is not unprotected, though: `PaperTrader.on_candle` resolves the setup's stop/target
    against each candle's range, so a paper row without a bracket names the protection that
    mechanism actually enforces -- the stop the tranche was sized against, when the row recorded
    one (`initial_stop`, since 0.12.2 / #520). A tranche that predates that migration, or a DCA
    leg that never got one, has no number to show and says so instead of inventing one.
    """
    if pos.has_bracket or mode != "paper":
        return "bracketed" if pos.has_bracket else "NO bracket"
    if pos.initial_stop is not None:
        return f"paper stop {pos.initial_stop}"
    return "n/a -- paper resolves stop/target on candle touch"


def render_human(report: StatusReport) -> list[str]:
    """The `keel status` (default, non-`--json`) rendering, as a list of lines -- kept as a pure
    function of the report so it is testable without a CliRunner."""
    lines: list[str] = []
    lines.append(f"mode: {report.mode}")
    lines.append(
        "kill_switch: ENGAGED (halted)" if report.kill_switch_engaged else "kill_switch: clear"
    )
    session_line = _session_line(report.market_session)
    if session_line is not None:
        lines.append(session_line)
    a = report.autonomy
    if not a.profile_readable:
        lines.append(
            "  WARNING: profile row unreadable -- reporting autonomy as OFF (safe reading)."
        )
    autonomy_line = "autonomy: ON -- orders placed WITHOUT asking" if a.live else "autonomy: off"
    lines.append(autonomy_line)
    if a.autonomous and not a.live:
        lines.append(f"  (was ON but LAPSED at {a.autonomous_until})")
    elif a.live and a.autonomous_until is not None:
        lines.append(f"  lapses at {a.autonomous_until}")

    lines.append("")
    lines.append(f"equity_state_mode: {report.equity_state_mode or 'unknown'}")
    hwm = report.high_water_mark if report.high_water_mark is not None else "unknown"
    lines.append(f"high_water_mark: {hwm}")
    dd_total = report.drawdown_total_pct if report.drawdown_total_pct is not None else "unknown"
    dd_weekly = report.drawdown_weekly_pct if report.drawdown_weekly_pct is not None else "unknown"
    lines.append(
        f"drawdown: total={dd_total} (ceiling {report.max_total_dd_pct}) "
        f"weekly={dd_weekly} (ceiling {report.max_weekly_dd_pct})"
    )
    lines.append(f"rail11 (drawdown breaker): {report.rail11_status}")
    lines.append(_rail17_line(report.withdrawal_attestation, report.mode != "paper"))
    if report.mode == "paper":
        lines.append(f"paper_cash_usdc: {report.paper_cash_usdc}")

    lines.append("")
    if not report.open_positions:
        lines.append("open positions: no open positions")
    else:
        lines.append(f"open positions ({len(report.open_positions)}):")
        for pos in report.open_positions:
            bracket = _bracket_display(pos, report.mode)
            lines.append(
                f"  [{pos.id}] {pos.product_id} qty={pos.qty} entry={pos.entry_price} "
                f"opened_at={pos.opened_at} rule={pos.rule_name} ({bracket})"
            )

    lines.append("")
    counts = " ".join(f"{status}={count}" for status, count in sorted(report.rule_counts.items()))
    lines.append(f"rules: {counts or 'none'}")
    for rule in report.live_rules:
        lines.append(
            f"  live [{rule.id}] {rule.kind} product={rule.product_id} params={rule.params}"
        )

    lines.append("")
    lines.append("data freshness:")
    for f in report.data_freshness:
        if f.last_ts is None:
            lines.append(f"  {f.product_id}: no data")
        else:
            lines.append(f"  {f.product_id} ({f.granularity}): {_human_age(f.age_sec or 0)}")

    if report.subscriptions:
        lines.append("")
        lines.append("subscriptions:")
        for s in report.subscriptions:
            cap = "unlimited" if s.effective_cap is None else str(s.effective_cap)
            lines.append(
                f"  {s.venue}: tier={s.tier_name} status={s.effective_status} cap={cap}"
            )

    return lines


def _report_to_jsonable(report: StatusReport) -> dict[str, Any]:
    return asdict(report)


# -- the command ----------------------------------------------------------------------------


@click.command("status")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.pass_context
def status_cmd(ctx: click.Context, as_json: bool) -> None:
    """Operator-observability snapshot of the agent's state -- read-only, no broker call.

    Shows mode, the kill-switch, autonomy, Rail 11 drawdown/equity state, rail 17's
    withdrawal-attestation state (fresh with time remaining, or EXPIRED with how long ago and
    the command that re-attests), open positions, rule counts, and per-product data freshness,
    all read straight from the local DB and config. This is the interim of the deferred
    `keel status` TUI: same underlying report (`--json` is its forward-compatible shape), just
    rendered to the terminal.

    `--json` deliberately skips the disclaimer footer every other command prints
    (`with_disclaimer`): it exists for scripting/the future TUI, and a trailing line of prose
    after the JSON would break every consumer that does `json.loads(output)`.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    report = gather_status(repo, config, now_ts=int(time.time()))

    if as_json:
        click.echo(json.dumps(_report_to_jsonable(report), indent=2, default=str))
        return

    for line in render_human(report):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)
