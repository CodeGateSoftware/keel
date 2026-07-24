"""`keel tui` -- a live, read-only, full-screen operator dashboard.

`keel status` (`keel/commands/status.py`) was deliberately built as the substrate for this: its
`gather_status(repo, config, now_ts) -> StatusReport` is a pure, broker-free report, and
`keel status --json` is its forward-compatible shape. `keel tui` is strictly a *view* over that
same report -- it never re-derives Rail 11, freshness, or autonomy logic, only styles it.

Like `keel status`, this NEVER calls the broker or touches the network, and it is strictly
read-only: it cannot confirm, kill, or arm anything. Acting from the dashboard (confirming a
pending order, kill/resume) is a separate, larger feature with its own gating design.

Two layers, mirroring `status.py`'s split:

- `build_screen` is a PURE function of `(StatusReport, now_ts)` -> `list[ScreenLine]`, directly
  unit-testable without curses or a CliRunner. `render_plain` and `_freshness_style` are pure
  helpers built the same way.
- `_paint` (curses rendering), `run_once` (single-frame, `--once`/pipes/CI), and `run_live`
  (the auto-refreshing `curses.wrapper` loop) are the thin I/O layer. `curses` is imported
  lazily inside the functions that need it, so this module stays importable -- and
  `build_screen`/`render_plain` tests stay portable -- even where a real terminal is absent.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import click

from keel.commands._common import DISCLAIMER, _load_cfg, _open_repo
from keel.commands.status import StatusReport, _human_age, gather_status
from keel.config import Config
from keel.data.repository import Repository
from keel.types import Granularity

# -- the pure screen model (the testable core) --------------------------------------------------


@dataclass(frozen=True)
class ScreenLine:
    text: str
    style: str  # one of: "heading" | "normal" | "ok" | "alert" | "warn" | "muted"


# Period, in seconds, of each configured candle granularity -- keyed by `Granularity.value` (the
# same strings `ProductFreshness.granularity` stores) so `_freshness_style` never has to import
# the enum member itself, just compare strings.
_GRANULARITY_PERIOD_SEC: dict[str, int] = {
    Granularity.ONE_MINUTE.value: 60,
    Granularity.FIVE_MINUTE.value: 300,
    Granularity.FIFTEEN_MINUTE.value: 900,
    Granularity.ONE_HOUR.value: 3600,
    Granularity.SIX_HOUR.value: 21600,
    Granularity.ONE_DAY.value: 86400,
}


def _freshness_style(granularity: str | None, age_sec: int | None) -> str:
    """`"ok"` when a product's newest candle is within 2x its own granularity's period, `"warn"`
    when it is staler than that -- or when there is no local data / unknown granularity to begin
    with (a daily series a couple of days old is fine; a couple of *periods* old is stale)."""
    if granularity is None or age_sec is None:
        return "warn"
    period = _GRANULARITY_PERIOD_SEC.get(granularity)
    if period is None:
        return "warn"
    return "warn" if age_sec > 2 * period else "ok"


def _blank() -> ScreenLine:
    return ScreenLine("", "normal")


def _title_lines(report: StatusReport, now_ts: int) -> list[ScreenLine]:
    return [ScreenLine(f"keel · {report.mode} mode · now={now_ts}", "heading")]


def _kill_switch_lines(report: StatusReport) -> list[ScreenLine]:
    if report.kill_switch_engaged:
        return [ScreenLine("kill_switch: ENGAGED (halted)", "alert")]
    return [ScreenLine("kill_switch: clear", "ok")]


def _autonomy_lines(report: StatusReport) -> list[ScreenLine]:
    a = report.autonomy
    lines: list[ScreenLine] = []
    if not a.profile_readable:
        lines.append(
            ScreenLine(
                "  WARNING: profile row unreadable -- reporting autonomy as OFF (safe reading).",
                "warn",
            )
        )
    if a.live:
        lines.append(ScreenLine("autonomy: ON -- orders placed WITHOUT asking", "alert"))
    else:
        lines.append(ScreenLine("autonomy: off", "muted"))
    if a.autonomous and not a.live:
        lines.append(ScreenLine(f"  (was ON but LAPSED at {a.autonomous_until})", "muted"))
    elif a.live and a.autonomous_until is not None:
        lines.append(ScreenLine(f"  lapses at {a.autonomous_until}", "muted"))
    return lines


def _rail11_style(status: str) -> str:
    if status == "HALTED":
        return "alert"
    if status == "unknown":
        return "warn"
    return "ok"


def _equity_lines(report: StatusReport) -> list[ScreenLine]:
    lines: list[ScreenLine] = []
    mode_text = f"equity_state_mode: {report.equity_state_mode or 'unknown'}"
    lines.append(ScreenLine(mode_text, "normal"))
    hwm = report.high_water_mark if report.high_water_mark is not None else "unknown"
    lines.append(ScreenLine(f"high_water_mark: {hwm}", "normal"))
    dd_total = report.drawdown_total_pct if report.drawdown_total_pct is not None else "unknown"
    dd_weekly = report.drawdown_weekly_pct if report.drawdown_weekly_pct is not None else "unknown"
    lines.append(
        ScreenLine(
            f"drawdown: total={dd_total} (ceiling {report.max_total_dd_pct}) "
            f"weekly={dd_weekly} (ceiling {report.max_weekly_dd_pct})",
            "normal",
        )
    )
    rail11_text = f"rail11 (drawdown breaker): {report.rail11_status}"
    lines.append(ScreenLine(rail11_text, _rail11_style(report.rail11_status)))
    if report.mode == "paper":
        lines.append(ScreenLine(f"paper_cash_usdc: {report.paper_cash_usdc}", "normal"))
    return lines


def _open_position_lines(report: StatusReport) -> list[ScreenLine]:
    lines: list[ScreenLine] = []
    if not report.open_positions:
        lines.append(ScreenLine("open positions: no open positions", "normal"))
        return lines
    lines.append(ScreenLine(f"open positions ({len(report.open_positions)}):", "normal"))
    for pos in report.open_positions:
        bracket_note = "bracketed" if pos.has_bracket else "NO bracket"
        row_style = "normal" if pos.has_bracket else "warn"
        lines.append(
            ScreenLine(
                f"  [{pos.id}] {pos.product_id} qty={pos.qty} entry={pos.entry_price} "
                f"opened_at={pos.opened_at} rule={pos.rule_name} ({bracket_note})",
                row_style,
            )
        )
    return lines


def _rule_lines(report: StatusReport) -> list[ScreenLine]:
    lines: list[ScreenLine] = []
    counts = " ".join(f"{status}={count}" for status, count in sorted(report.rule_counts.items()))
    lines.append(ScreenLine(f"rules: {counts or 'none'}", "normal"))
    for rule in report.live_rules:
        lines.append(
            ScreenLine(
                f"  live [{rule.id}] {rule.kind} product={rule.product_id} params={rule.params}",
                "alert",
            )
        )
    return lines


def _freshness_lines(report: StatusReport) -> list[ScreenLine]:
    lines: list[ScreenLine] = [ScreenLine("data freshness:", "normal")]
    for f in report.data_freshness:
        style = _freshness_style(f.granularity, f.age_sec)
        if f.last_ts is None:
            lines.append(ScreenLine(f"  {f.product_id}: no data", style))
        else:
            age_text = f"  {f.product_id} ({f.granularity}): {_human_age(f.age_sec or 0)}"
            lines.append(ScreenLine(age_text, style))
    return lines


def _subscription_lines(report: StatusReport) -> list[ScreenLine]:
    if not report.subscriptions:
        return []
    lines: list[ScreenLine] = [ScreenLine("subscriptions:", "normal")]
    for s in report.subscriptions:
        cap = "unlimited" if s.effective_cap is None else str(s.effective_cap)
        sub_text = f"  {s.venue}: tier={s.tier_name} status={s.effective_status} cap={cap}"
        lines.append(ScreenLine(sub_text, "normal"))
    return lines


def build_screen(report: StatusReport, now_ts: int) -> list[ScreenLine]:
    """Turn a `StatusReport` into styled rows -- a PURE function of the report, reusing every
    logic decision (Rail 11, freshness, autonomy) `gather_status` already made. Never re-derives
    status; only styles it."""
    lines: list[ScreenLine] = []
    lines.extend(_title_lines(report, now_ts))
    lines.extend(_kill_switch_lines(report))
    lines.extend(_autonomy_lines(report))
    lines.append(_blank())
    lines.extend(_equity_lines(report))
    lines.append(_blank())
    lines.extend(_open_position_lines(report))
    lines.append(_blank())
    lines.extend(_rule_lines(report))
    lines.append(_blank())
    lines.extend(_freshness_lines(report))
    sub_lines = _subscription_lines(report)
    if sub_lines:
        lines.append(_blank())
        lines.extend(sub_lines)
    lines.append(_blank())
    # Deliberately interval-independent: `build_screen` doesn't know the poll interval, so it
    # cannot say "refreshing every Ns" without threading that through its signature. The live
    # loop is free to show its own interval-bearing status line if desired.
    lines.append(ScreenLine("q quit · read-only (no broker)", "muted"))
    return lines


def render_plain(report: StatusReport, now_ts: int) -> list[str]:
    """The `.text` of each `build_screen` line, styles dropped -- drives `--once` and any
    non-tty use."""
    return [line.text for line in build_screen(report, now_ts)]


# -- render + loop (thin I/O) --------------------------------------------------------------------


def _style_attrs() -> dict[str, int]:
    """Map each `ScreenLine.style` to a curses attribute bitmask. Uses only attribute constants
    that are safe to read without a real terminal having called `initscr()` (`A_BOLD`, `A_DIM`,
    ...); colour pairs are layered on top only when `curses.has_colors()` can be queried without
    raising (i.e. a real terminal did initialise), so this stays callable against a fake stdscr
    in tests. Any `curses.error` while querying/initialising colour support is swallowed -- the
    attribute-only styling below is still a coherent, if colourless, rendering."""
    import curses

    attrs: dict[str, int] = {
        "heading": curses.A_BOLD,
        "alert": curses.A_BOLD | curses.A_REVERSE,
        "warn": curses.A_BOLD | curses.A_UNDERLINE,
        "ok": curses.A_NORMAL,
        "normal": curses.A_NORMAL,
        "muted": curses.A_DIM,
    }
    try:
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_RED, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            attrs["alert"] |= curses.color_pair(1)
            attrs["warn"] |= curses.color_pair(2)
            attrs["ok"] |= curses.color_pair(3)
    except curses.error:
        pass
    return attrs


def _paint(stdscr: Any, lines: list[ScreenLine]) -> None:
    """Paint styled `lines` into a curses window, clipped to its current size so a tiny terminal
    never raises `curses.error`. Testable against a fake `stdscr` (records `addstr(y, x, text,
    attr)`, has `getmaxyx()`) -- no real terminal required."""
    import curses

    height, width = stdscr.getmaxyx()
    attrs = _style_attrs()
    stdscr.erase()
    for y, line in enumerate(lines):
        if y >= height:
            break
        max_width = max(width - 1, 0)
        text = line.text[:max_width]
        attr = attrs.get(line.style, curses.A_NORMAL)
        try:
            stdscr.addstr(y, 0, text, attr)
        except curses.error:
            # Classic bottom-right-corner write: some terminals raise when the cursor would
            # advance past the last cell. Never fatal to the dashboard.
            pass
    stdscr.refresh()


OpenState = Callable[[], "tuple[Repository, Config]"]
NowFn = Callable[[], int]
Echo = Callable[[str], None]


def run_once(open_state: OpenState, now_fn: NowFn, echo: Echo) -> None:
    """Render a single frame and hand each line to `echo` -- drives `--once` (pipes/CI) and is
    directly testable with fakes, no CliRunner or terminal needed."""
    repo, config = open_state()
    now_ts = now_fn()
    report = gather_status(repo, config, now_ts)
    for line in render_plain(report, now_ts):
        echo(line)


def run_live(open_state: OpenState, now_fn: NowFn, interval: float) -> None:
    """The auto-refreshing dashboard: `curses.wrapper` a loop that re-opens the repo (via
    `open_state`) every poll -- so it reflects writes committed by a separate `keel agent`
    process -- gathers a fresh report, paints it, then waits up to `interval` seconds for a
    keypress. Quits on `q`/`Q`; a `KeyboardInterrupt` (Ctrl-C) exits gracefully rather than
    dumping a traceback onto a terminal `curses.wrapper` may not have fully restored."""
    import curses

    def _loop(stdscr: Any) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            # Not every terminal has a hideable cursor -- never fatal to the dashboard.
            pass
        while True:
            now_ts = now_fn()
            try:
                # `Repository` exposes no public connection handle or `close()` (only the
                # private `_conn`), so there is nothing safe to close here each poll -- `repo`
                # simply falls out of scope and is garbage-collected.
                repo, config = open_state()
                report = gather_status(repo, config, now_ts)
                _paint(stdscr, build_screen(report, now_ts))
            except Exception as exc:
                # A transient read error (e.g. `sqlite3.OperationalError: database is locked`
                # from a concurrent `keel agent` writer) must never kill the dashboard --
                # paint an alert line and keep polling. `KeyboardInterrupt` is not caught here
                # (it isn't an `Exception`) so Ctrl-C still reaches the outer handler below.
                _paint(stdscr, [ScreenLine(f"status read failed: {exc} -- retrying...", "alert")])
            stdscr.timeout(int(interval * 1000))
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break

    try:
        curses.wrapper(_loop)
    except KeyboardInterrupt:
        pass


# -- the command ----------------------------------------------------------------------------


@click.command("tui")
@click.option(
    "--interval",
    type=float,
    default=5.0,
    show_default=True,
    help="Seconds between refreshes.",
)
@click.option(
    "--once",
    is_flag=True,
    default=False,
    help="Render a single frame to stdout and exit (no curses; for pipes/CI).",
)
@click.pass_context
def tui_cmd(ctx: click.Context, interval: float, once: bool) -> None:
    """Live, read-only, full-screen operator dashboard -- never calls the broker.

    A view over the same `gather_status` report `keel status` prints once: mode, kill-switch,
    autonomy, Rail 11 drawdown/equity state, open positions, rule counts, per-product data
    freshness, and subscriptions, auto-refreshing on an interval. Strictly read-only: it cannot
    confirm, kill, or arm anything -- for that, use the dedicated commands.

    `--once` renders a single frame to stdout and exits without touching curses, for pipes/CI,
    matching `status`'s scripting-friendliness (and prints the disclaimer footer after the
    frame). The default, interactive path owns the whole screen via `curses.wrapper` and re-opens
    the repo every poll so it reflects writes committed by a separate `keel agent` process; quit
    with `q`.
    """
    if interval <= 0:
        raise click.ClickException("--interval must be > 0")

    def open_state() -> tuple[Repository, Config]:
        return _open_repo(ctx), _load_cfg(ctx)

    now_fn: NowFn = lambda: int(time.time())  # noqa: E731

    if once:
        run_once(open_state, now_fn, click.echo)
        click.echo("")
        click.echo(DISCLAIMER)
        return

    run_live(open_state, now_fn, interval)
