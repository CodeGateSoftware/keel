"""`keel insights` -- a READ-ONLY reporting surface over the paper-forward's track record.

Two subcommands:

- `keel insights summary` -- per-rule promotion-gate distance (how close is a `paper`-status
  rule to clearing its floor) plus an account-level projection of `keel status`'s own report.
- `keel insights journal` -- a chronological, filterable trade journal built off the fee-honest
  `trade_outcomes` ledger, enriched with R-multiples from the paper track record where a match
  can be found.

**Strictly read-only.** This module places no orders, touches no rails/guards, and adds no new
`Repository` write method -- it is a pure VIEW over `gather_status`/`StatusReport`
(`keel.commands.status`), `Repository`'s existing read methods, `paper.track_record`, and the
promotion machinery (`keel.strategy.promotion`). It re-derives nothing rail11/drawdown/floor-
related; every such value is projected verbatim from an existing output, exactly like `keel tui`
does off `gather_status`.

Two layers, matching `status.py`/`tui.py`:

- Pure builders (`build_*`) are functions of `(Repository, Config, StatusReport, now_ts, ...)` ->
  a frozen report dataclass. No click dependency, directly unit-testable.
- The `insights` click group opens repo/config via the standard `_open_repo`/`_load_cfg` seams
  (same as every other command), calls the builders, and renders -- either `click.echo` lines
  (default, ending in the shared `DISCLAIMER` footer) or `--json` (no trailing prose, so
  `json.loads(output)` always succeeds).

**NAMING CAUTION:** there is an unrelated `journal` TABLE in `keel/db.py` (a manual discipline
diary: emotion_score, rules_followed, etc.) with no accessor. `keel insights journal` never reads
or writes it -- it is built entirely off `trade_outcomes` (see `Repository.get_trade_outcomes`).

**On `--mode`:** `paper.track_record` is inherently paper-mode/R-aware regardless of a rule's
current lifecycle `status` (it just replays `orders(mode='paper')` for a given rule name), so
`--mode` does not change *how* stats are computed -- it selects *which* rules the report scopes
to: `paper` (the default) shows rules still in the proving pipeline (`candidate`/`paper` status,
i.e. "how close to promotion"); `live` shows rules already promoted and trading (`live` status,
i.e. "how is the live edge holding up"). Every rule falls into exactly one bucket.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import click

from keel import agent as agent_mod
from keel.commands._common import DISCLAIMER, _load_cfg, _open_repo
from keel.commands.status import StatusReport, _human_age, gather_status
from keel.commands.tui import _human_dt
from keel.config import Config
from keel.data.repository import Repository
from keel.strategy.paper import track_record
from keel.strategy.promotion import (
    PromotionConfig,
    can_promote,
    floor_for_class,
    promotion_class_of,
)
from keel.strategy.rules.base import Rule

# -- the pure report shapes -----------------------------------------------------------------


@dataclass(frozen=True)
class JournalEntry:
    closed_at: int | None
    opened_at: int
    rule_name: str | None
    product_id: str
    qty: Decimal
    entry_fill: Decimal
    exit_fill: Decimal | None
    pnl_net: Decimal | None
    fees: Decimal | None
    r_multiple: Decimal | None
    is_dca: bool
    outcome: str


@dataclass(frozen=True)
class JournalReport:
    now_ts: int
    mode: str
    entries: list[JournalEntry]
    total_count: int
    """The full filtered (closed) entry count BEFORE `--limit` truncates for display."""
    filters: dict[str, Any]


@dataclass(frozen=True)
class GateDistance:
    rule_name: str
    promotion_class: str
    n_trades: int
    min_trades: int
    trades_remaining: int
    win_rate: float
    min_win_rate: float
    realized_rr: Decimal | None
    min_rr: Decimal
    expectancy: Decimal
    min_expectancy: Decimal
    passing: bool
    blocking_reasons: list[str]


@dataclass(frozen=True)
class RuleTrackRecord:
    rule_name: str
    status: str
    promotion_class: str
    n_trades: int
    win_rate: float
    avg_win: Decimal
    avg_loss: Decimal
    realized_rr: Decimal | None
    expectancy: Decimal
    profit_factor: Decimal
    max_drawdown: Decimal
    significant: bool
    """`n_trades >= 30` -- the sample-size floor below which win-rate/expectancy are not yet
    statistically distinguishable from random entry (see `strategy.promotion`'s own KB citation).
    """
    gate: GateDistance | None
    """`None` for any status other than `paper` -- a `candidate` hasn't backtested yet and a
    `live`/`disabled` rule has already cleared (or been pulled from) the gate this measures."""


@dataclass(frozen=True)
class AccountSummary:
    mode: str
    equity_state_mode: str | None
    high_water_mark: Decimal | None
    drawdown_total_pct: Decimal | None
    drawdown_weekly_pct: Decimal | None
    max_total_dd_pct: Decimal
    max_weekly_dd_pct: Decimal
    rail11_status: str
    paper_cash_usdc: Decimal | None


@dataclass(frozen=True)
class InsightsReport:
    now_ts: int
    account: AccountSummary
    rules: list[RuleTrackRecord]
    closed_trade_count: int


# -- pure builders --------------------------------------------------------------------------


def build_account_summary(report: StatusReport) -> AccountSummary:
    """Pure projection off `StatusReport` -- never re-derives rail11/drawdown, just copies the
    fields `gather_status` already computed."""
    return AccountSummary(
        mode=report.mode,
        equity_state_mode=report.equity_state_mode,
        high_water_mark=report.high_water_mark,
        drawdown_total_pct=report.drawdown_total_pct,
        drawdown_weekly_pct=report.drawdown_weekly_pct,
        max_total_dd_pct=report.max_total_dd_pct,
        max_weekly_dd_pct=report.max_weekly_dd_pct,
        rail11_status=report.rail11_status,
        paper_cash_usdc=report.paper_cash_usdc,
    )


def _realized_rr_for_display(stats: Any) -> Decimal | None:
    """`avg_win / |avg_loss|`, or `None` when there are no losing trades yet.

    Deliberately diverges from `strategy.promotion._realized_rr` (which returns `Infinity` in
    that case, because a promotion-gate check needs a comparable scalar). This is a display
    value, not a gate decision -- `None` ("no losses yet, nothing to measure against") is the
    honest reading, not a real-looking `Infinity`.
    """
    if stats.avg_loss == 0:
        return None
    return stats.avg_win / abs(stats.avg_loss)


def build_gate_distance(
    rule: Rule, stats: Any, default_floor: PromotionConfig
) -> GateDistance:
    """How far `stats` is from `rule`'s promotion floor (its class's floor, or `default_floor`
    for classes with no code-defined override)."""
    promotion_class = promotion_class_of(rule)
    floor = floor_for_class(promotion_class, default=default_floor)
    passing, reasons = can_promote(stats, floor)
    return GateDistance(
        rule_name=rule.name,
        promotion_class=promotion_class,
        n_trades=stats.n_trades,
        min_trades=floor.min_trades,
        trades_remaining=max(0, floor.min_trades - stats.n_trades),
        win_rate=stats.win_rate,
        min_win_rate=floor.min_win_rate,
        realized_rr=_realized_rr_for_display(stats),
        min_rr=floor.min_rr,
        expectancy=stats.expectancy,
        min_expectancy=floor.min_expectancy,
        passing=passing,
        blocking_reasons=reasons,
    )


def build_rule_track_record(
    row: dict[str, Any], stats: Any, default_floor: PromotionConfig
) -> RuleTrackRecord:
    """Aggregate `row` (a `repo.get_rules()` row) + its paper `stats` into a `RuleTrackRecord`.

    Degrades gracefully -- promotion_class="default", gate=None -- when `row["kind"]` is no
    longer in `RULE_REGISTRY` (`agent._build_rule` raises `ValueError` for that), rather than
    crashing the whole report over one stale row.
    """
    try:
        rule = agent_mod._build_rule(row)
        promotion_class = promotion_class_of(rule)
    except ValueError:
        rule = None
        promotion_class = "default"

    gate: GateDistance | None = None
    if row["status"] == "paper" and rule is not None:
        gate = build_gate_distance(rule, stats, default_floor)

    return RuleTrackRecord(
        rule_name=row["kind"],
        status=row["status"],
        promotion_class=promotion_class,
        n_trades=stats.n_trades,
        win_rate=stats.win_rate,
        avg_win=stats.avg_win,
        avg_loss=stats.avg_loss,
        realized_rr=_realized_rr_for_display(stats),
        expectancy=stats.expectancy,
        profit_factor=stats.profit_factor,
        max_drawdown=stats.max_drawdown,
        significant=stats.n_trades >= 30,
        gate=gate,
    )


_PAPER_PIPELINE_STATUSES = {"candidate", "paper"}


def build_insights_report(
    repo: Repository,
    config: Config,
    status_report: StatusReport,
    now_ts: int,
    *,
    mode: str = "paper",
    rule_filter: str | None = None,
) -> InsightsReport:
    """Assemble the full `InsightsReport` -- read-only, no re-derivation of anything
    `gather_status` already computed."""
    default_floor = PromotionConfig(
        min_trades=config.promotion.min_trades,
        min_expectancy=config.promotion.min_expectancy,
        min_rr=config.promotion.min_rr,
        min_win_rate=float(config.promotion.min_win_rate),
    )

    rows = repo.get_rules()
    if rule_filter is not None:
        rows = [r for r in rows if r["kind"] == rule_filter]
    if mode == "live":
        rows = [r for r in rows if r["status"] == "live"]
    else:
        rows = [r for r in rows if r["status"] in _PAPER_PIPELINE_STATUSES]

    rules = [
        build_rule_track_record(row, track_record(repo, row["kind"]), default_floor)
        for row in rows
    ]

    return InsightsReport(
        now_ts=now_ts,
        account=build_account_summary(status_report),
        rules=rules,
        closed_trade_count=len(repo.get_trade_outcomes()),
    )


_TS_MATCH_TOLERANCE_SEC = 1
"""Trade-outcome timestamps and paper-trade timestamps come from the same bar clock in
practice, but are matched with a small tolerance rather than exact equality -- robust to any
off-by-a-second rounding between the two write paths without risking a false cross-rule match
(a wrong-rule collision on the exact same instant is vanishingly unlikely)."""


def _approx(a: int, b: int) -> bool:
    return abs(a - b) <= _TS_MATCH_TOLERANCE_SEC


def _match_trade(entries_for_rule: list[Any], opened_at: int, closed_at: int) -> Any | None:
    for trade in entries_for_rule:
        if trade.exit_ts is None:
            continue
        if _approx(trade.entry_ts, opened_at) and _approx(trade.exit_ts, closed_at):
            return trade
    return None


def _journal_entry_from_outcome(
    row: dict[str, Any], trades_by_rule: dict[str, list[Any]], repo: Repository
) -> JournalEntry:
    if row["is_dca"]:
        r_multiple = None
        outcome = "dca"
    else:
        r_multiple = None
        outcome = None
        rule_name = row["rule_name"]
        if rule_name:
            if rule_name not in trades_by_rule:
                trades_by_rule[rule_name] = track_record(repo, rule_name).trades
            matched = _match_trade(trades_by_rule[rule_name], row["opened_at"], row["closed_at"])
            if matched is not None:
                r_multiple = matched.r_multiple
                outcome = matched.outcome
        if outcome is None:
            pnl = row["pnl_net"]
            outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "scratch"

    return JournalEntry(
        closed_at=row["closed_at"],
        opened_at=row["opened_at"],
        rule_name=row["rule_name"],
        product_id=row["product_id"],
        qty=row["qty"],
        entry_fill=row["entry_fill"],
        exit_fill=row["exit_fill"],
        pnl_net=row["pnl_net"],
        fees=row["fees"],
        r_multiple=r_multiple,
        is_dca=row["is_dca"],
        outcome=outcome,
    )


def _journal_entry_from_open_position(pos: Any) -> JournalEntry:
    return JournalEntry(
        closed_at=None,
        opened_at=pos.opened_at,
        rule_name=pos.rule_name,
        product_id=pos.product_id,
        qty=pos.qty,
        entry_fill=pos.entry_price,
        exit_fill=None,
        pnl_net=None,
        fees=None,
        r_multiple=None,
        is_dca=False,
        outcome="open",
    )


def build_journal_report(
    repo: Repository,
    status_report: StatusReport,
    now_ts: int,
    *,
    rule: str | None = None,
    asset: str | None = None,
    since_ts: int | None = None,
    until_ts: int | None = None,
    limit: int | None = None,
    include_open: bool = False,
) -> JournalReport:
    """Build the chronological (oldest-first, internally) trade journal.

    Sourced from `repo.get_trade_outcomes(since_ts)` (the fee-honest, mode-agnostic, DCA-aware
    ledger) rather than `paper.track_record` directly -- that keeps the journal meaningful in
    `live` mode too, where there is no paper track record at all. Each row is enriched with
    `r_multiple`/`outcome` by matching it to a paper `Trade` for the same rule on
    `(opened_at≈entry_ts, closed_at≈exit_ts)`; on no match (or for a DCA row, which has no stop
    to measure R against), `r_multiple` stays `None` and `outcome` falls back to a plain
    pnl-sign read (DCA rows are labelled `"dca"` regardless of pnl sign).

    `total_count` is the full filtered (closed) count BEFORE `--limit` truncates; `--limit`
    keeps the MOST RECENT `limit` entries (the tail of the oldest-first list) while staying
    oldest-first internally -- `render_journal` is what reverses to most-recent-first for
    display.
    """
    outcomes = repo.get_trade_outcomes(since_ts=since_ts)

    filtered = []
    for row in outcomes:
        if until_ts is not None and row["closed_at"] > until_ts:
            continue
        if rule is not None and row["rule_name"] != rule:
            continue
        if asset is not None and row["product_id"] != asset:
            continue
        filtered.append(row)

    trades_by_rule: dict[str, list[Any]] = {}
    entries = [_journal_entry_from_outcome(row, trades_by_rule, repo) for row in filtered]

    total_count = len(entries)
    if limit is not None:
        entries = entries[-limit:]

    if include_open:
        for pos in status_report.open_positions:
            if rule is not None and pos.rule_name != rule:
                continue
            if asset is not None and pos.product_id != asset:
                continue
            entries.append(_journal_entry_from_open_position(pos))

    filters = {
        "rule": rule,
        "asset": asset,
        "since_ts": since_ts,
        "until_ts": until_ts,
        "limit": limit,
        "include_open": include_open,
    }

    return JournalReport(
        now_ts=now_ts,
        mode=status_report.mode,
        entries=entries,
        total_count=total_count,
        filters=filters,
    )


# -- render (human-readable) --------------------------------------------------------------------

_SMALL_SAMPLE_NOTE = (
    "n<30: not yet statistically distinguishable from random entry -- do not read this as a "
    "proven edge"
)

_TWO_DP = Decimal("0.01")


def _quantized(x: Any) -> Any:
    """2dp-round a `Decimal` for HUMAN display only -- `--json` stays full precision
    (`json.dumps(..., default=str)` never goes through this).

    Passes through anything that isn't a finite `Decimal` unchanged: a sentinel string
    ("n/a (no losses yet)", "n/a", "dca"), `None`, or a non-finite `Decimal` (`Infinity`,
    which `BacktestResult.profit_factor`/a zero-loss realized-rr can legitimately be, and
    which `.quantize()` itself refuses to round) all render exactly as before.
    """
    if isinstance(x, Decimal) and x.is_finite():
        return x.quantize(_TWO_DP)
    return x


# One shared helper covers both money and ratio fields (both are just "round this Decimal to
# 2dp for a human, leave any sentinel alone") -- two names kept at call sites purely for the
# reader, per the field groupings above.
_money = _quantized
_ratio = _quantized


def render_summary(report: InsightsReport) -> list[str]:
    """The `keel insights summary` (default, non-`--json`) rendering, as a list of lines."""
    lines: list[str] = []
    a = report.account
    lines.append(f"mode: {a.mode}")
    lines.append(f"equity_state_mode: {a.equity_state_mode or 'unknown'}")
    hwm = a.high_water_mark if a.high_water_mark is not None else "unknown"
    lines.append(f"high_water_mark: {hwm}")
    dd_total = a.drawdown_total_pct if a.drawdown_total_pct is not None else "unknown"
    dd_weekly = a.drawdown_weekly_pct if a.drawdown_weekly_pct is not None else "unknown"
    lines.append(
        f"drawdown: total={dd_total} (ceiling {a.max_total_dd_pct}) "
        f"weekly={dd_weekly} (ceiling {a.max_weekly_dd_pct})"
    )
    lines.append(f"rail11 (drawdown breaker): {a.rail11_status}")
    if a.mode == "paper":
        lines.append(f"paper_cash_usdc: {a.paper_cash_usdc}")

    lines.append("")
    lines.append(f"closed trades (all rules, all time): {report.closed_trade_count}")

    lines.append("")
    if not report.rules:
        lines.append(
            "no rule track record yet -- no rules seeded, or no closed paper trades in scope."
        )
        return lines

    lines.append(f"rule track record ({len(report.rules)}):")
    for r in report.rules:
        rr = r.realized_rr if r.realized_rr is not None else "n/a (no losses yet)"
        lines.append(
            f"  [{r.rule_name}] status={r.status} class={r.promotion_class} n={r.n_trades} "
            f"win_rate={r.win_rate:.1%} avg_win={_money(r.avg_win)} avg_loss={_money(r.avg_loss)} "
            f"rr={_ratio(rr)} expectancy={_money(r.expectancy)} "
            f"profit_factor={_ratio(r.profit_factor)} max_dd={_money(r.max_drawdown)}"
        )
        if not r.significant:
            lines.append(f"    {_SMALL_SAMPLE_NOTE}")
        if r.gate is not None:
            verdict = "PASSING" if r.gate.passing else "blocked"
            lines.append(
                f"    gate: {verdict} -- trades_remaining={r.gate.trades_remaining} "
                f"(n>={r.gate.min_trades}, win_rate>={r.gate.min_win_rate}, "
                f"rr>={r.gate.min_rr}, expectancy>{r.gate.min_expectancy})"
            )
            for reason in r.gate.blocking_reasons:
                lines.append(f"      - {reason}")
        elif r.status == "paper":
            lines.append("    gate: unavailable (rule kind not recognized -- stale row?)")
        elif r.status == "candidate":
            lines.append("    gate: needs a backtest pass before it has a paper track record")

    return lines


def render_journal(report: JournalReport) -> list[str]:
    """The `keel insights journal` (default, non-`--json`) rendering, most-recent-first."""
    lines: list[str] = []
    lines.append(f"mode: {report.mode}")
    active_filters = {k: v for k, v in report.filters.items() if v not in (None, False)}
    filters_desc = ", ".join(f"{k}={v}" for k, v in active_filters.items()) or "none"
    lines.append(f"filters: {filters_desc}")
    # `report.total_count` is the pre-`--limit` CLOSED count; `report.entries` (once
    # `--include-open` appends live positions) mixes closed rows with `outcome == "open"` ones,
    # so the numerator here must exclude those or the line lies (e.g. "4 of 3 closed trades").
    shown_closed = sum(1 for e in report.entries if e.outcome != "open")
    open_shown = len(report.entries) - shown_closed
    count_line = f"showing {shown_closed} of {report.total_count} closed trades"
    if open_shown:
        count_line += f" (+{open_shown} open)"
    lines.append(count_line)
    lines.append("")

    if not report.entries:
        lines.append("no closed trades yet.")
        return lines

    for e in reversed(report.entries):
        if e.outcome == "open":
            lines.append(
                f"  OPEN  {e.product_id} qty={_money(e.qty)} entry={_money(e.entry_fill)} "
                f"opened_at={_human_dt(e.opened_at)} rule={e.rule_name}"
            )
        elif e.is_dca:
            age = _human_age(max(report.now_ts - (e.closed_at or report.now_ts), 0))
            lines.append(
                f"  [{_human_dt(e.closed_at or 0)} / {age}] {e.product_id} DCA "
                f"qty={_money(e.qty)} pnl_net={_money(e.pnl_net)} -- "
                f"DCA: no stop, excluded from R/expectancy"
            )
        else:
            r_text = e.r_multiple if e.r_multiple is not None else "n/a"
            age = _human_age(max(report.now_ts - (e.closed_at or report.now_ts), 0))
            lines.append(
                f"  [{_human_dt(e.closed_at or 0)} / {age}] {e.product_id} rule={e.rule_name} "
                f"outcome={e.outcome} qty={_money(e.qty)} pnl_net={_money(e.pnl_net)} "
                f"R={_ratio(r_text)}"
            )

    return lines


# -- the commands ---------------------------------------------------------------------------


def _parse_ts(value: str) -> int:
    """Accept either a unix timestamp or a bare `YYYY-MM-DD` date (read as UTC midnight)."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        return int(dt.timestamp())
    except ValueError as exc:
        raise click.BadParameter(
            f"invalid timestamp {value!r} -- expected a unix integer or YYYY-MM-DD"
        ) from exc


@click.group("insights")
def insights_group() -> None:
    """Read-only reporting: promotion-gate distance and a filterable trade journal.

    Purely a VIEW over the local DB (via `gather_status`, `Repository`'s read methods, and
    `paper.track_record`) -- like `keel status`/`keel tui`, this never calls the broker and
    never writes anything.
    """


@insights_group.command("summary")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.option("--rule", "rule_filter", default=None, help="Restrict to one rule kind.")
@click.option(
    "--mode",
    "mode",
    type=click.Choice(["paper", "live"]),
    default="paper",
    help="paper: candidate/paper-status rules (promotion pipeline). live: live-status rules.",
)
@click.pass_context
def summary_cmd(ctx: click.Context, as_json: bool, rule_filter: str | None, mode: str) -> None:
    """Per-rule promotion-gate distance + an account-level snapshot.

    `--json` skips the disclaimer footer (like `keel status --json`) so it stays a clean,
    scriptable payload.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    now_ts = int(time.time())
    status_report = gather_status(repo, config, now_ts)
    report = build_insights_report(
        repo, config, status_report, now_ts, mode=mode, rule_filter=rule_filter
    )

    if as_json:
        click.echo(json.dumps(asdict(report), indent=2, default=str))
        return

    for line in render_summary(report):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)


@insights_group.command("journal")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit machine-readable JSON.")
@click.option("--rule", "rule_filter", default=None, help="Restrict to one rule kind.")
@click.option("--asset", "asset_filter", default=None, help="Restrict to one product_id.")
@click.option("--limit", "limit", type=int, default=None, help="Cap rows shown (most recent).")
@click.option("--since", "since_raw", default=None, help="Unix timestamp or YYYY-MM-DD.")
@click.option("--until", "until_raw", default=None, help="Unix timestamp or YYYY-MM-DD.")
@click.option(
    "--include-open",
    "include_open",
    is_flag=True,
    default=False,
    help="Append currently-open positions as outcome=open rows.",
)
@click.pass_context
def journal_cmd(
    ctx: click.Context,
    as_json: bool,
    rule_filter: str | None,
    asset_filter: str | None,
    limit: int | None,
    since_raw: str | None,
    until_raw: str | None,
    include_open: bool,
) -> None:
    """A chronological, filterable trade journal off the fee-honest `trade_outcomes` ledger.

    `--json` skips the disclaimer footer (like `keel status --json`) so it stays a clean,
    scriptable payload.
    """
    repo = _open_repo(ctx)
    config = _load_cfg(ctx)
    now_ts = int(time.time())
    status_report = gather_status(repo, config, now_ts)
    since_ts = _parse_ts(since_raw) if since_raw is not None else None
    until_ts = _parse_ts(until_raw) if until_raw is not None else None

    report = build_journal_report(
        repo,
        status_report,
        now_ts,
        rule=rule_filter,
        asset=asset_filter,
        since_ts=since_ts,
        until_ts=until_ts,
        limit=limit,
        include_open=include_open,
    )

    if as_json:
        click.echo(json.dumps(asdict(report), indent=2, default=str))
        return

    for line in render_journal(report):
        click.echo(line)
    click.echo("")
    click.echo(DISCLAIMER)
