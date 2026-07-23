"""`keel status` -- the interim, read-only operator dashboard for a paper-forward.

The paper-mode-fidelity spec explicitly deferred a dedicated status command as a follow-up
("A dedicated `keel status` command is deferred as a follow-up"); this is that follow-up. Its
job is narrow: let an operator running a paper-forward see the agent's state at a glance,
**purely from the local DB and config** -- mode, kill-switch, autonomy, Rail 11 drawdown/equity
state, open positions, rule counts, and per-product data freshness. It NEVER calls the broker;
that is the whole point (`monitor`/`agent` are the commands that touch the network).

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

from keel.commands._common import DISCLAIMER, _load_cfg, _open_repo
from keel.commands._products import _default_sim_products
from keel.config import Config
from keel.data.repository import Repository
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
    paper_cash_usdc: Decimal | None
    open_positions: list[OpenPositionStatus]
    rule_counts: dict[str, int]
    live_rules: list[RuleSummary]
    data_freshness: list[ProductFreshness]
    subscriptions: list[SubscriptionStatusRow]


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
        paper_cash_usdc=(
            repo.get_state("paper_cash_usdc") if config.auto_trade.mode == "paper" else None
        ),
        open_positions=[_open_position_status(row) for row in repo.get_open_positions()],
        rule_counts=rule_counts,
        live_rules=live_rules,
        data_freshness=_data_freshness(repo, config, now_ts),
        subscriptions=_subscription_rows(repo, config, now_ts),
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


def render_human(report: StatusReport) -> list[str]:
    """The `keel status` (default, non-`--json`) rendering, as a list of lines -- kept as a pure
    function of the report so it is testable without a CliRunner."""
    lines: list[str] = []
    lines.append(f"mode: {report.mode}")
    lines.append(
        "kill_switch: ENGAGED (halted)" if report.kill_switch_engaged else "kill_switch: clear"
    )
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
    if report.mode == "paper":
        lines.append(f"paper_cash_usdc: {report.paper_cash_usdc}")

    lines.append("")
    if not report.open_positions:
        lines.append("open positions: no open positions")
    else:
        lines.append(f"open positions ({len(report.open_positions)}):")
        for pos in report.open_positions:
            bracket = "bracketed" if pos.has_bracket else "NO bracket"
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

    Shows mode, the kill-switch, autonomy, Rail 11 drawdown/equity state, open positions, rule
    counts, and per-product data freshness, all read straight from the local DB and config. This
    is the interim of the deferred `keel status` TUI: same underlying report (`--json` is its
    forward-compatible shape), just rendered to the terminal.

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
