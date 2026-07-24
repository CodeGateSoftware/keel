"""`keel tui` -- a live, full-screen operator dashboard, with a browsable help overlay and a
handful of gated actions.

`keel status` (`keel/commands/status.py`) was deliberately built as the substrate for this: its
`gather_status(repo, config, now_ts) -> StatusReport` is a pure, broker-free report, and
`keel status --json` is its forward-compatible shape. `keel tui` is strictly a *view* over that
same report -- it never re-derives Rail 11, freshness, or autonomy logic, only styles it.

v1 was strictly read-only, like `keel status`. v2 (this module) relaxes that: the live loop can
now toggle autonomy, trigger a data fetch, and refresh on demand -- but with the SAME asymmetric
gating the rest of the CLI already enforces (spec-wide principle): a de-risking action (autonomy
OFF) is immediate and ungated; an action that *adds* capability (autonomy ON) demands a typed
`yes` from a human at a terminal, exactly like `keel autonomy on`. The hard rails are untouched --
autonomy only changes *who is asked*, never *what is allowed*. `fetch` only ever pulls candle
data (money-safe, no orders). `--once` stays a static, non-interactive snapshot.

Two layers, mirroring `status.py`'s split:

- `build_screen`, `build_help_screen`, `_visible_slice`, `_footer_lines`, `_freshness_style`,
  `toggle_autonomy` and `_guarded` are all PURE (or take only injected fakes), directly
  unit-testable without curses, a CliRunner, or the network. `render_plain` is the same, dropping
  styles.
- `_paint` (curses rendering), `run_once` (single-frame, `--once`/pipes/CI), `run_live` (the
  auto-refreshing `curses.wrapper` loop), and `_confirm_arm_autonomy` (the cooked-mode typed-`yes`
  prompt) are the thin I/O layer. `curses` is imported lazily inside the functions that need it,
  so this module stays importable -- and the pure-function tests stay portable -- even where a
  real terminal is absent.
"""

from __future__ import annotations

import sys
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


def _human_dt(ts: int) -> str:
    """Local-time `YYYY-MM-DD HH:MM:SS` for a unix timestamp -- readable in the title, each open
    position's `opened_at`, and the autonomy lapsed/lapses-at lines. Freshness keeps its own
    relative `_human_age` ("4h ago"); this is for absolute points in time."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _title_lines(report: StatusReport, now_ts: int) -> list[ScreenLine]:
    return [ScreenLine(f"keel · {report.mode} mode · now={_human_dt(now_ts)}", "heading")]


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
        lapsed_text = f"  (was ON but LAPSED at {_human_dt(a.autonomous_until)})"
        lines.append(ScreenLine(lapsed_text, "muted"))
    elif a.live and a.autonomous_until is not None:
        lines.append(ScreenLine(f"  lapses at {_human_dt(a.autonomous_until)}", "muted"))
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
                f"opened_at={_human_dt(pos.opened_at)} rule={pos.rule_name} ({bracket_note})",
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


def _footer_lines() -> list[ScreenLine]:
    """The keybinding hint bar shown at the bottom of the normal-mode dashboard. Deliberately
    interval-independent (see `build_screen`'s note) and pure, so it's directly testable."""
    return [
        ScreenLine(
            "keys: [q] quit  [h] help  [r] refresh  [a] autonomy  [f] fetch", "muted"
        )
    ]


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
    lines.extend(_footer_lines())
    return lines


def render_plain(report: StatusReport, now_ts: int) -> list[str]:
    """The `.text` of each `build_screen` line, styles dropped -- drives `--once` and any
    non-tty use."""
    return [line.text for line in build_screen(report, now_ts)]


def build_help_screen() -> list[ScreenLine]:
    """A titled, scrollable help overlay documenting every keybinding and the safety notes for
    the two capability-adding actions. PURE -- deliberately longer than a small terminal so the
    `_visible_slice` scrolling the live loop drives against it actually matters."""
    lines: list[ScreenLine] = [ScreenLine("keel tui -- help", "heading"), _blank()]

    def _row(text: str) -> None:
        lines.append(ScreenLine(text, "normal"))

    def _note(text: str) -> None:
        lines.append(ScreenLine(text, "muted"))

    _row("Normal mode")
    _row("  q            quit")
    _row("  h  /  ?      open this help")
    _row("  r            refresh now (poll immediately, instead of waiting for the interval)")
    _row("  a            toggle autonomy (arm / disarm the agent placing orders unattended)")
    _row("  f            fetch all data (pull candles for every configured product)")
    _note("               can pull up to 5y of candles; the dashboard freezes until it finishes")
    _note("               (Ctrl-C aborts the whole TUI, not just the fetch)")
    lines.append(_blank())
    _row("Help mode (this screen)")
    _row("  up / k       scroll up one line")
    _row("  down / j     scroll down one line")
    _row("  PgUp         scroll up one page")
    _row("  PgDn         scroll down one page")
    _row("  Home         jump to the top")
    _row("  End          jump to the bottom")
    _row("  q / Esc / h / ?    close help, back to the dashboard")
    lines.append(_blank())
    _row("Safety notes")
    _note(
        "  autonomy OFF is immediate and ungated -- de-risking must never be obstructed, so"
    )
    _note("  turning it off never asks for confirmation, exactly like `keel autonomy off`.")
    lines.append(_blank())
    _note(
        "  autonomy ON is DANGEROUS: once armed, the agent places rule-generated orders"
    )
    _note(
        "  WITHOUT asking first, subject to all the same hard rails as every other mode. Arming"
    )
    _note(
        "  from here suspends the screen and requires a typed \"yes\" at the terminal, exactly"
    )
    _note("  like `keel autonomy on` -- it is never armed silently or on a keystroke alone.")
    lines.append(_blank())
    _note(
        "  fetch is money-safe: it only pulls candle history from the venue's public market-data"
    )
    _note("  endpoints. It places no orders and touches no rails.")
    lines.append(_blank())
    _row("Every action shows a one-line result at the bottom of the dashboard until the next")
    _row("action replaces it.")
    lines.append(_blank())
    _row("Press q, Esc, h or ? now to return to the dashboard.")
    return lines


def _visible_slice(lines: list[ScreenLine], offset: int, height: int) -> list[ScreenLine]:
    """The `height`-line window of `lines` starting at `offset`, clamped so `offset` never runs
    past what would leave a partial screen at the end (or before the start). PURE -- never raises
    on a tiny/zero `height` or an `offset` far past the end of `lines`."""
    if height <= 0:
        return []
    max_offset = max(0, len(lines) - height)
    offset = max(0, min(offset, max_offset))
    return lines[offset : offset + height]


# -- actions (injectable, unit-testable without curses/network) ----------------------------------


def toggle_autonomy(repo: Any, now_ts: int, confirm_fn: Callable[[], bool]) -> str:
    """Toggle the agent's autonomy, honouring the same asymmetric gating `keel autonomy` enforces:
    turning OFF de-risks and is immediate; turning ON adds capability and only happens if
    `confirm_fn()` returns `True` (the live loop's `confirm_fn` is `_confirm_arm_autonomy`, a
    cooked-mode typed-`yes` prompt -- but this function takes it as an injected callable so it's
    testable with a stub, no curses or terminal involved). Arms with no expiry, matching `keel
    autonomy on`'s default."""
    profile = repo.get_profile()
    if profile.is_autonomous(now_ts):
        repo.set_autonomous(False, now_ts)
        return "autonomy -> OFF (every order will ask first)"
    if confirm_fn():
        repo.set_autonomous(True, now_ts)
        return "autonomy -> ON (orders placed WITHOUT asking)"
    return "autonomy unchanged (arming cancelled)"


def _guarded(label: str, fn: Callable[[], str]) -> str:
    """Run `fn`, returning its result -- or, if it raises, `"{label} failed: {exc}"` instead of
    letting the exception kill the live loop. Only `Exception` is caught: a `KeyboardInterrupt`
    (Ctrl-C) must still propagate."""
    try:
        return fn()
    except Exception as exc:
        return f"{label} failed: {exc}"


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

#: How long (seconds) an action's toast (`message`) stays painted on the dashboard before it is
#: cleared -- an unbounded toast can read as *current* state long after it stopped being true
#: (e.g. "autonomy -> ON" still showing after autonomy has since lapsed).
_MESSAGE_TTL_SEC = 12


def run_once(open_state: OpenState, now_fn: NowFn, echo: Echo) -> None:
    """Render a single frame and hand each line to `echo` -- drives `--once` (pipes/CI) and is
    directly testable with fakes, no CliRunner or terminal needed."""
    repo, config = open_state()
    now_ts = now_fn()
    report = gather_status(repo, config, now_ts)
    for line in render_plain(report, now_ts):
        echo(line)


def _message_style(message: str) -> str:
    """`"alert"` for a message that reports a failure OR arms autonomy ON (the one dangerous
    transition -- it must never read as reassuring green); `"warn"` for a cancelled/unchanged
    action; `"ok"` for everything else (autonomy OFF, fetch complete) -- purely cosmetic, so a
    quick glance at the toast's colour tells you which it was."""
    lowered = message.lower()
    if "-> on" in lowered or "without asking" in lowered or "failed" in lowered:
        return "alert"
    if "cancelled" in lowered or "unchanged" in lowered:
        return "warn"
    return "ok"


def _confirm_arm_autonomy(stdscr: Any, config: Config) -> bool:
    """The live loop's `confirm_fn` for `toggle_autonomy`'s OFF->ON direction: suspends curses
    (`def_prog_mode` -> `endwin`), runs the SAME `_require_interactive_confirmation` gate `keel
    autonomy on` uses -- so the arm prompt shows the same decisive facts (mode=..., allowlist=...)
    and demands a typed `yes` from a human at a terminal -- then restores the screen
    (`reset_prog_mode` -> `refresh`). Fails CLOSED -- any exception anywhere in this (including
    while restoring the screen) returns `False` rather than arming, since arming is the one
    direction that must never happen silently."""
    import curses

    from keel.commands._common import _require_interactive_confirmation

    try:
        curses.def_prog_mode()
        curses.endwin()
        try:
            _require_interactive_confirmation(
                "turn autonomy ON",
                f"Orders will be placed with NO further prompt until you turn it off "
                f"(mode={config.auto_trade.mode}, allowlist={config.allowlist}). "
                f"Tip: prefer a supervised window via `keel autonomy on --for-hours N`.",
            )
            return True
        finally:
            curses.reset_prog_mode()
            stdscr.refresh()
    except Exception:
        return False


def _do_fetch(open_state: OpenState, now_fn: NowFn) -> str:
    """Fetch fresh candle history for every allowlisted product, money-safe (data only, never
    places an order). Lazy-imports the fetch primitives from `keel.cli`/`keel.data.history`/
    `keel.commands._common`/`keel.commands._products` to avoid a `cli` <-> `tui` import cycle at
    module load time. Thin I/O -- not unit-tested directly, only smoke-tested via `run_live`."""
    from keel.cli import _SIM_GRANULARITIES
    from keel.commands._common import _build_broker
    from keel.commands._products import _default_sim_products
    from keel.data import history as history_mod

    repo, config = open_state()
    products = _default_sim_products(config)
    client = _build_broker(config)
    now_ts = now_fn()
    years = 5  # matches `keel fetch --years`'s own default
    history_mod.ensure_history(
        client,
        repo,
        products,
        _SIM_GRANULARITIES,
        years,
        now_ts,
        sleep_fn=time.sleep,
    )
    return f"fetch complete ({len(products)} products, {years}y history)"


def run_live(open_state: OpenState, now_fn: NowFn, interval: float) -> None:
    """The auto-refreshing, interactive dashboard: `curses.wrapper` a loop that re-opens the repo
    (via `open_state`) every poll -- so it reflects writes committed by a separate `keel agent`
    process -- gathers a fresh report, paints it, then waits up to `interval` seconds for a
    keypress.

    Two modes: `normal` (the dashboard, plus a transient one-line `message` toast from the last
    action) and `help` (a scrolled window of `build_help_screen()`, via `_visible_slice`).
    Normal-mode keys: `q`/`Q` quit; `h`/`?` open help; `r` refresh now; `a` toggle autonomy
    (`toggle_autonomy`, gated by `_confirm_arm_autonomy` on the OFF->ON direction only); `f`
    fetch all data (`_do_fetch`, money-safe). `a` and `f` are both wrapped in `_guarded` so a
    failure becomes a toast, never a crash. Help-mode keys scroll (`up`/`k`, `down`/`j`,
    `PgUp`/`PgDn`, `Home`/`End`) or close back to normal (`q`/`Esc`/`h`/`?`).

    Quits on `q`/`Q` in normal mode; a `KeyboardInterrupt` (Ctrl-C) exits gracefully rather than
    dumping a traceback onto a terminal `curses.wrapper` may not have fully restored."""
    import curses

    def _loop(stdscr: Any) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            # Not every terminal has a hideable cursor -- never fatal to the dashboard.
            pass

        mode = "normal"
        help_offset = 0
        message: str | None = None
        message_ts = 0

        while True:
            if mode == "help":
                help_lines = build_help_screen()
                height, _width = stdscr.getmaxyx()
                _paint(stdscr, _visible_slice(help_lines, help_offset, height))
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (curses.KEY_UP, ord("k")):
                    help_offset -= 1
                elif ch in (curses.KEY_DOWN, ord("j")):
                    help_offset += 1
                elif ch == curses.KEY_PPAGE:
                    help_offset -= max(height - 1, 1)
                elif ch == curses.KEY_NPAGE:
                    help_offset += max(height - 1, 1)
                elif ch == curses.KEY_HOME:
                    help_offset = 0
                elif ch == curses.KEY_END:
                    help_offset = len(help_lines)
                elif ch in (ord("q"), 27, ord("h"), ord("?")):
                    mode = "normal"
                    help_offset = 0
                help_offset = max(0, min(help_offset, max(0, len(help_lines) - height)))
                continue

            # mode == "normal"
            now_ts = now_fn()
            try:
                # `Repository` exposes no public connection handle or `close()` (only the
                # private `_conn`), so there is nothing safe to close here each poll -- `repo`
                # simply falls out of scope and is garbage-collected.
                repo, config = open_state()
                report = gather_status(repo, config, now_ts)
                lines = build_screen(report, now_ts)
                if message is not None and now_ts - message_ts > _MESSAGE_TTL_SEC:
                    message = None
                if message is not None:
                    lines = [*lines, ScreenLine(message, _message_style(message))]
                _paint(stdscr, lines)
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
            if ch in (ord("h"), ord("?")):
                mode = "help"
                help_offset = 0
                continue
            if ch == ord("r"):
                continue
            if ch == ord("a"):

                def _do_toggle() -> str:
                    toggle_repo, cfg = open_state()
                    return toggle_autonomy(
                        toggle_repo, now_fn(), lambda: _confirm_arm_autonomy(stdscr, cfg)
                    )

                message = _guarded("autonomy", _do_toggle)
                message_ts = now_fn()
                continue
            if ch == ord("f"):
                _paint(stdscr, [ScreenLine("fetching data... please wait", "normal")])
                message = _guarded("fetch", lambda: _do_fetch(open_state, now_fn))
                message_ts = now_fn()
                continue

    try:
        curses.wrapper(_loop)
    except KeyboardInterrupt:
        pass
    except curses.error as exc:
        # `curses.wrapper` can raise before the loop even runs -- e.g. `cbreak() returned ERR`
        # when stdin/stdout is not a real, controlling terminal (a captured pipe, a harness that
        # only fakes a TTY). The `_stdio_is_interactive` pre-check in `tui_cmd` catches the common
        # case up front; this is the belt-and-braces for a TTY that passes `isatty()` yet still
        # can't be put into cbreak mode. Turn the raw traceback into a clean, actionable message.
        raise click.ClickException(
            f"keel tui could not start a terminal UI ({exc}). It needs a real interactive "
            "terminal; run it directly in one, or use `keel tui --once` for a one-shot snapshot."
        ) from exc


def _stdio_is_interactive() -> bool:
    """True only when BOTH stdin and stdout are real TTYs -- curses needs to read keypresses AND
    own the screen, so either one being a pipe/redirect means the full-screen loop cannot run.
    Kept as its own function so tests can patch it (and so the check reads as one intent)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


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
    """Live, full-screen operator dashboard, with a browsable help menu and a few gated actions.

    A view over the same `gather_status` report `keel status` prints once: mode, kill-switch,
    autonomy, Rail 11 drawdown/equity state, open positions, rule counts, per-product data
    freshness, and subscriptions, auto-refreshing on an interval. Never places an order and never
    touches the network except when explicitly asked to (`f`). Press `h`/`?` for the in-app help
    (every keybinding and the safety notes); `a` toggles autonomy (turning it OFF is instant,
    turning it ON needs a typed "yes" at the terminal, exactly like `keel autonomy on`); `f`
    fetches fresh candle history for every configured product (money-safe: no orders); `r`
    refreshes immediately. Quit with `q`.

    `--once` renders a single, static frame to stdout and exits without touching curses, for
    pipes/CI, matching `status`'s scripting-friendliness (and prints the disclaimer footer after
    the frame) -- none of the interactive actions are available there. The default, interactive
    path owns the whole screen via `curses.wrapper` and re-opens the repo every poll so it
    reflects writes committed by a separate `keel agent` process.
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

    if not _stdio_is_interactive():
        raise click.ClickException(
            "keel tui needs an interactive terminal (a real TTY on both stdin and stdout). "
            "Run it directly in a terminal, or use `keel tui --once` for a one-shot snapshot."
        )

    run_live(open_state, now_fn, interval)
