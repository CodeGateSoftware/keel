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

- `build_screen`, `build_help_screen`, `_visible_slice`, `_scroll_offset`, `_footer_lines`,
  `_freshness_style`, `toggle_autonomy` and `_guarded` are all PURE (or take only injected
  fakes), directly unit-testable without curses, a CliRunner, or the network. `render_plain` is
  the same, dropping styles.
- `_paint` (curses rendering), `run_once` (single-frame, `--once`/pipes/CI), `run_live` (the
  auto-refreshing `curses.wrapper` loop), and `_confirm_arm_autonomy` (the cooked-mode typed-`yes`
  prompt) are the thin I/O layer. `curses` is imported lazily inside the functions that need it,
  so this module stays importable -- and the pure-function tests stay portable -- even where a
  real terminal is absent.

v3 (this revision) wires the allowlist-admission workflow (`keel/commands/admission.py`, already
fully built and covered by its own tests) into three more overlays, reusing that module's report
builders/renderers VERBATIM rather than reimplementing any of it -- exactly the same discipline
`i` insights already keeps toward `keel/commands/insights.py`:

- `s` **screen** -- `build_admission_screen_overlay` over `build_screen_report`: the current
  allowlist's admission verdicts. OFFLINE, DB reads only.
- `p` **propose** -- `build_propose_overlay` over `build_propose_view`: screens the newest
  shortlist file in `config.proposals_dir` (or names why there is none). OFFLINE, DB + local
  filesystem reads only.
- `d` **discover** -- `build_discover_overlay` over `build_discover_report`: proposes NEW
  candidates from the venue's own product list. This is the one of the three overlays that needs
  the network, and it is the THIRD deliberate network exception in this dashboard -- the other
  two being the automatic ~30s live-balance refresh (`_refresh_balance`, a real `get_accounts`
  call that has been firing on its own cadence since v3) and `f` fetch. Counting only fetch, as
  this docstring used to, understates by one and tells an operator the dashboard is offline
  between keypresses when it is not. Opening the overlay makes no call at all (it renders an
  ARMED, not-yet-run explanation), and only an explicit Enter keypress *inside* the overlay
  triggers `_do_discover_report`'s one `_build_broker(config).list_products()` call. The result
  is then HELD -- every following poll while the overlay stays open repaints the same cached
  result (or error) rather than re-fetching, and closing the overlay discards it, so reopening is
  armed but not yet run again.

None of the three attests, admits, or trades -- they only ever PROPOSE or REPORT, and cannot
themselves put an asset on `allowlist` in `config.yaml`. `attest` -- the human judgment the
whole gate rests on -- stopped being CLI-only when the console's Compliance menu grew the
typed attest form (C3, `keel/commands/compliance_console.py`): it IS invokable from the
console now, from the menu and from the scout browser's `a` step, but never on a keypress
alone -- the form ends in a typed confirmation (type the ASSET CODE back; withdrawals attest
types its own CLI phrase, `yes`), so the safety is the phrase, not CLI-only-ness.

v4 (this revision) adds `v` **activity** -- `build_activity_overlay` over
`keel.commands.activity.build_activity_feed`, reusing that module's pure grouping/summarising
VERBATIM exactly as `i`/`s`/`p`/`d` reuse theirs. It answers the one question none of the other
five could: *what has keel been DOING*. Every overlay above this one reports STATE, and state is
what looks dead when nothing trades -- a deployment that has run flawlessly for three weeks and
correctly declined every setup shows exactly the same zeroes as one that died on day one. The
activity feed is the narrative instead: one row per engine cycle, newest first, expandable to the
events inside it, so a run of quiet cycles reads as the positive observation it is rather than as
an absence.

Its source is the structured JSONL engine log -- NOT the database, and not a new table. See
`keel/commands/activity.py`'s own docstring for the full argument, but the short of it is that a
new table would start EMPTY on the very deployment this exists to explain, while the log is
already months deep. No schema change, no migration, no engine change.

**That makes `v` the one overlay that reads a file rather than the DB, so it is worth saying
plainly why it is admissible.** This dashboard's iron rule -- stated for `s` screen above -- is
"DB reads only; never builds a broker, never touches the network." Reading a local log file is
NEITHER of the two things that rule forbids: no broker is constructed, no socket opened, no name
resolved. The rule exists so that opening an overlay can never place an order, spend money, or
block on a remote host, and a bounded read of a file on the same disk violates none of that. It
is the same latitude `p` propose already takes to read `config.proposals_dir`, and the network
exception count in this module stands unchanged at three.

The read is BOUNDED -- 1 MiB of the log's tail, 5000 lines, 200 cycles, all named constants in
`activity.py` -- because the log grows without limit and this overlay rebuilds every poll. A
dashboard whose responsiveness degrades with how long the deployment has been running would be a
worse bug than the one this feature fixes.

v5 SCOPES that feed to the current local calendar day by default, with `t` inside the overlay
cycling `today` -> `7 days` -> `all`. "What has keel been doing" means today unless asked
otherwise, and a fortnight of scrollback is not an answer to it. Two consequences are handled in
`activity.py` rather than here, and both are the point of the change rather than trimming around
it: the scope is a parameter that RESETS to `today` on every open (a widened view answers one
question once; it does not become tomorrow's default), and an empty "today" -- the normal state
of a once-a-day deployment every morning before 09:00 -- renders `describe_empty_scope`, which
names when keel last ran and when the next cycle is due. A blank panel there would be worse
than the dead-looking state dashboard the whole feature exists to fix, since a blank panel and a
dead agent look exactly alike.

v6 (issue #388 C2, the PRD's operator-console slice 2) wraps the whole thing in the CONSOLE
SHELL -- `keel/commands/console.py`, whose own docstring owns the design. The dashboard stays
the landing screen and every existing mode is unchanged: the shell adds an `m` menu mode over
the PRD §3 tree (future slices' entries render a "lands in C3/C4/C5" notice), a Profile menu
that rebinds the console's config+db pair through the same `_load_cfg`/`_open_repo` loaders
every CLI command uses (LIVE guarded by an explicit y/N, never O3's typed contract), and the
session banner (O9): a two-line header on EVERY screen -- active deployment, then the recorded
market session + clock (24/7, or OPEN/CLOSED with the recorded next open/close, or CLOCK
UNAVAILABLE fail-loud when the record is absent or stale) -- composed from `keel.agent`'s
recording alone. `run_live` takes the shell as an OPTIONAL `console_binding`; a caller that
passes none gets the pre-C2 dashboard byte-for-byte, which is what keeps every existing test --
and `--once`, and any embedded consumer -- on the unchanged path. The shell adds NO network
touch of its own: the banner reads the repo and the adapter's offline capabilities
declaration, profile switching reads local files, and the three deliberate network exceptions
below are still the only three.
"""

from __future__ import annotations

import sys
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from keel.commands._common import DISCLAIMER
from keel.commands.activity import (
    ACTIVITY_HEADER,
    DEFAULT_ACTIVITY_SCOPE,
    ActivityFeed,
    build_activity_feed,
    cycle_style,
    describe_empty_scope,
    describe_status,
    event_style,
    footer_notes,
    next_activity_scope,
    render_cycle_row,
    render_event_row,
    scope_headline,
)
from keel.commands.admission import (
    DiscoverReport,
    ProposeView,
    ScreenReport,
    build_discover_report,
    build_propose_view,
    build_screen_report,
    render_discover_report,
    render_propose_view,
    render_screen_report,
)
from keel.commands.status import (
    StatusReport,
    _human_age,
    _rail17_line,
    _session_line,
    gather_status,
)
from keel.config import Config
from keel.data.repository import Repository
from keel.types import Granularity
from keel.version import _package_version

if TYPE_CHECKING:
    # `keel.commands.insights` imports `_human_dt` back from this module, so importing it at
    # module load time would be a circular import -- these names are only used in type
    # annotations here (never evaluated at runtime, `from __future__ import annotations` keeps
    # them as strings), and every call site below lazy-imports the real symbols it needs.
    from keel.commands.insights import InsightsReport, JournalReport

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


def _freshness_style(
    granularity: str | None, age_sec: int | None, *, market_closed: bool = False
) -> str:
    """`"ok"` when a product's newest candle is within 2x its own granularity's period, `"warn"`
    when it is staler than that -- or when there is no local data / unknown granularity to begin
    with (a daily series a couple of days old is fine; a couple of *periods* old is stale).

    `market_closed` (the report's own session answer, closed AND inside its trust window --
    `MarketSessionStatus.defused`) downgrades the AGE-based warn to `"muted"`: a behind
    series during a closure is the expected weekend shape, and painting it warn while the
    session line two rows up says CLOSED muted would be one screen disagreeing with itself.
    The no-data/unknown-granularity warn is deliberately NOT downgraded -- a closed venue
    still serves history, so a cold cache is a pipeline problem, not a session artifact (the
    `fetch --check` rule, carried into colour)."""
    if granularity is None or age_sec is None:
        return "warn"
    period = _GRANULARITY_PERIOD_SEC.get(granularity)
    if period is None:
        return "warn"
    if age_sec > 2 * period:
        return "muted" if market_closed else "warn"
    return "ok"


def _blank() -> ScreenLine:
    return ScreenLine("", "normal")


def _human_dt(ts: int) -> str:
    """Local-time `YYYY-MM-DD HH:MM:SS` for a unix timestamp -- readable in the title, each open
    position's `opened_at`, and the autonomy lapsed/lapses-at lines. Freshness keeps its own
    relative `_human_age` ("4h ago"); this is for absolute points in time."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _short_version(raw: str) -> str:
    """`v<major>.<minor>.<patch>` from a full version string (`0.5.2` -> `v0.5.2`).

    The patch segment is shown because major.minor alone cannot tell two deployments apart: every
    release in the 0.5 line rendered as `v0.5`, so an operator glancing at the header could not
    see whether the box was running the build they just shipped or the one before it.

    Three degradations, each preferring the most information it can still vouch for:

    * **Build metadata is stripped from the patch.** A release version is `0.5.2+79f35b9e73d5`, so
      `parts[2]` is `2+79f35b9e73d5` -- not a digit. Taking it verbatim would print the whole
      commit hash into a header line budgeted for a version; rejecting it would drop the patch on
      precisely the shape a released build emits. Split on `+` and keep the numeric head.
    * **No patch segment** (`2.0`) -> `v2.0`. Show what exists rather than inventing a `.0` the
      version string never claimed.
    * **A non-numeric patch** (`0.5.2rc1`) -> `v0.5`. Falling all the way back to `v?` would throw
      away the two segments that did parse.

    Falls back to `v?` only when major/minor themselves are unparseable, so the header never shows
    a bare `unknown` and never raises.
    """
    parts = raw.split(".")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return "v?"
    head = f"v{parts[0]}.{parts[1]}"
    if len(parts) < 3:
        return head
    patch = parts[2].split("+", 1)[0]
    return f"{head}.{patch}" if patch.isdigit() else head


#: The short header version, resolved ONCE at import (it never changes within a run). Uses the
#: lightweight package-metadata reader, not `build_info()`, so no git subprocess runs per repaint.
_SHORT_VERSION = _short_version(_package_version())


def _title_lines(report: StatusReport, now_ts: int) -> list[ScreenLine]:
    return [ScreenLine(f"keel {_SHORT_VERSION} · {report.mode} mode", "heading")]


def _kill_switch_lines(report: StatusReport) -> list[ScreenLine]:
    if report.kill_switch_engaged:
        return [ScreenLine("kill_switch: ENGAGED (halted)", "alert")]
    return [ScreenLine("kill_switch: clear", "ok")]


def _market_session_style(state: str) -> str:
    """Colour for the session line, by what the state MEANS to an operator:

    * `open` is the working state -- `ok`, the same green a clear kill-switch gets.
    * `closed` is an EXPECTED state (every weekend, every holiday) -- `muted`, deliberately
      NOT warn/alert: a closed market is the system working as designed, and painting it
      yellow would spend the warning colour on ~2 days of every 7 until an operator stops
      looking at it. The line still names the skip and the alert relief, so the quiet is
      legible without being loud.
    * `clock_unavailable` is a degraded read the fail-closed posture is papering over --
      `warn`. Unlike a weekend it is never routine, and it is one clock outage away from
      every cycle skipping silently.

    No paper-mode divergence (unlike rail 17's): the session gate skips PAPER cycles too, so
    the same severity is truthful in every mode.
    """
    if state == "open":
        return "ok"
    if state == "clock_unavailable":
        return "warn"
    return "muted"


def _market_session_lines(report: StatusReport) -> list[ScreenLine]:
    """`render_human`'s exact session text, styled -- the `_rail17_line` discipline: the TUI
    and `keel status` render ONE string, so they can never disagree about whether the venue
    is closed. Nothing to say (a 24/7 venue, no cycle recorded) renders nothing, matching the
    text renderer byte for byte."""
    line = _session_line(report.market_session)
    if line is None:
        return []
    return [ScreenLine(line, _market_session_style(report.market_session.state or ""))]


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
        # An expiry is what makes this branch reachable (autonomy recorded ON, its deadline
        # passed), so `autonomous_until` is set here in practice. Guarded anyway because the
        # failure is silent rather than loud: `_human_dt(None)` does not raise --
        # `time.localtime(None)` means "now" -- so a missing deadline would render as having
        # lapsed at this very instant, which reads as fact.
        lapsed_at = _human_dt(a.autonomous_until) if a.autonomous_until is not None else "unknown"
        lines.append(ScreenLine(f"  (was ON but LAPSED at {lapsed_at})", "muted"))
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
    # `render_human`'s exact rail-17 text, so the TUI and `keel status` can never disagree
    # about whether entries are halted. Every state but `attested` fails rail 17 closed --
    # a halt -- so each is an alert; EXCEPT in paper mode, where the rail is not evaluated
    # and a stale attestation halts nothing (a permanently-red alert there is fatigue, not
    # information), so the same states downgrade to warn.
    rail17 = report.withdrawal_attestation
    rail17_evaluated = report.mode != "paper"
    style = "ok"
    if rail17.state != "attested":
        style = "alert" if rail17_evaluated else "warn"
    lines.append(ScreenLine(_rail17_line(rail17, rail17_evaluated), style))
    if report.mode == "paper":
        lines.append(ScreenLine(f"paper_cash_usdc: {report.paper_cash_usdc}", "normal"))
    return lines


@dataclass(frozen=True)
class AvailableBalance:
    """The live, real-account balance of `config.quote_currency` available to fund a buy --
    fetched via the exact same `_fetch_available_quote` rail 13 funds a buy against, so the TUI
    and the rail never disagree. `amount is None` means the balance could not be read (`error`
    explains why); `updated_ts` is when the read was attempted, whether or not it succeeded."""

    amount: Decimal | None
    quote: str
    updated_ts: int | None
    error: str | None


def _available_lines(available: AvailableBalance | None) -> list[ScreenLine]:
    """PURE: `available is None` (e.g. `--once`, which never touches the network) renders
    nothing. A successful read is an `"ok"` line naming the live account and when it was read; an
    unreadable balance (any broker/network failure, fail-soft) is a `"warn"` line with the
    reason -- never a crash, never a silently blank line.

    Labelled `"live account"`, not `"available to buy"`: in paper mode a buy spends
    `paper_cash_usdc`, not this real-account balance, so calling it "available to buy" would
    mislead an operator watching the paper dashboard."""
    if available is None:
        return []
    if available.amount is not None:
        # `updated_ts` is a separate field from `amount` and can be absent while the amount is
        # present; same silent-"now" hazard as the autonomy line above, and on a freshness
        # stamp specifically, where a wrong value is worse than an admitted missing one.
        as_of = _human_dt(available.updated_ts) if available.updated_ts is not None else "unknown"
        text = (
            f"live account: {available.amount:,.2f} {available.quote} available  ({as_of})"
        )
        return [ScreenLine(text, "ok")]
    return [ScreenLine(f"live account: unavailable -- {available.error}", "warn")]


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
    # The session answer the dashboard's own session line renders (`_market_session_lines`
    # reads the same `report.market_session`): closed AND still inside its trust window
    # (`defused`) mutes the staleness colour, so the cells and the line cannot disagree
    # about the same weekend. Anything else -- open, unreadable clock, an expired record --
    # keeps the ordinary warn.
    market_closed = (
        report.market_session.state == "closed" and report.market_session.defused
    )
    lines: list[ScreenLine] = [ScreenLine("data freshness:", "normal")]
    for f in report.data_freshness:
        style = _freshness_style(f.granularity, f.age_sec, market_closed=market_closed)
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
    interval-independent (see `build_screen`'s note) and pure, so it's directly testable.

    Two lines, not one: the first (kept byte-for-byte as it was before the admission overlays
    existed, so nothing that already reads it needs to change) is already close to 80 columns,
    and cramming more keys onto the end of it would either wrap on a normal terminal or
    silently truncate (`_paint` clips every line to the window width). A second line costs one
    more row of screen -- cheap, next to a footer line an operator can no longer read.

    That second line was labelled `admission:` while all three keys on it belonged to the
    admission workflow. v4's `v` activity did not, so the label became the accurate
    `overlays:` -- a footer that mis-files a key is worse than one that groups it loosely.
    The console shell's `m` (issue #388 C2) joins the same line, single-spaced so the row
    still fits the 80-column budget the two-line split exists to protect: `m` opens a
    whole mode (the menu), not an overlay, but it is a one-key destination exactly like
    the others, and an operator hunting for the console would not think to look anywhere
    but the footer."""
    return [
        ScreenLine(
            "keys: [q] quit  [h] help  [i] insights  [r] refresh  [a] autonomy  [f] fetch",
            "muted",
        ),
        ScreenLine(
            "overlays: [s] screen [p] propose [d] discover (network) [v] activity [m] menu",
            "muted",
        ),
    ]


def build_screen(
    report: StatusReport, now_ts: int, *, available: AvailableBalance | None = None
) -> list[ScreenLine]:
    """Turn a `StatusReport` into styled rows -- a PURE function of the report, reusing every
    logic decision (Rail 11, freshness, autonomy) `gather_status` already made. Never re-derives
    status; only styles it.

    `available` is the live "available to buy" balance (v3) -- keyword-only and defaulted to
    `None` so every existing caller (`--once`, `render_plain`, the whole pre-v3 test suite), which
    passes no `available`, renders EXACTLY as before and stays network-free. Only `run_live`
    threads a real `AvailableBalance` through, refreshed on its own slow cadence."""
    lines: list[ScreenLine] = []
    lines.extend(_title_lines(report, now_ts))
    lines.extend(_kill_switch_lines(report))
    lines.extend(_market_session_lines(report))
    lines.extend(_autonomy_lines(report))
    lines.append(_blank())
    lines.extend(_equity_lines(report))
    lines.extend(_available_lines(available))
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
    _row("  i            open the insights overlay (per-rule track record + promotion gates)")
    _row("  r            refresh now (poll immediately, instead of waiting for the interval)")
    _row("  a            toggle autonomy (arm / disarm the agent placing orders unattended)")
    _row("  f            fetch all data (pull candles for every configured product)")
    _note("               can pull up to 5y of candles; the dashboard freezes until it finishes")
    _note("               (Ctrl-C aborts the whole TUI, not just the fetch)")
    _row("  s            open the screen overlay (allowlist admission verdicts, read-only)")
    _row("  p            open the propose overlay (screens the newest shortlist file, read-only)")
    _row("  d            open the discover overlay (propose NEW candidates from the venue)")
    _note("               armed, not run, on open -- see 'Discover overlay' below")
    _row("  v            open the activity feed (what keel has been DOING, cycle by cycle)")
    _note("               reads the engine log, offline -- see 'Activity overlay' below")
    _note("               opens scoped to TODAY; press t inside it to widen")
    _row("  m            open the console menu (the shell over this dashboard)")
    _note("               deployment switching + the PRD's menu tree -- see 'Console menu' below")
    lines.append(_blank())
    _row("Which account is this?")
    _note("  paper and live are SEPARATE deployments -- separate config, database, allowlist,")
    _note("  caps and history -- and no figure on this screen describes the other one. Read")
    _note("  `equity_state_mode` to tell which is on screen; paper_cash_usdc appears in paper")
    _note("  mode only.")
    _note("  Switching is now in-app: press m for the console menu, then Profile -- each entry")
    _note("  is a config+db PAIR, selecting LIVE asks an explicit y/N first, and every screen's")
    _note("  banner names the active pair. The command line keeps the same rule, and --db")
    _note("  DEFAULTS to keel.db, so omitting it shows PAPER. The explicit pair still works:")
    _note("    keel --config config.live-sandbox.yaml --db keel-live.db tui")
    lines.append(_blank())
    _row("Console menu (m)")
    _note("  The shell is NAVIGATION: the PRD's menu tree with one entry per area. Dashboard")
    _note("  returns here; Profile switches deployment; Help opens this screen; every other")
    _note("  entry (Trading, Rules, Compliance, Data, Research, Account) is a placeholder")
    _note("  owned by a later console slice and says which one ('lands in C3'...C5) -- the")
    _note("  shell renders them so the tree is stable, but nothing in them is invokable yet.")
    _note("  Keys: up/k down/j move, Enter/Space select, 1-9 jump, q/Esc/m back to here.")
    _row("  Profile")
    _note("    Lists the four deployments by their config+db pair (the pairs the keel-paper /")
    _note("    keel-live / keel-paperhourly / keel-equities wrappers pin). Selecting one")
    _note("    rebinds config AND database together everywhere, in one action -- the header")
    _note("    banner on every screen names the active pair. Selecting LIVE asks an explicit")
    _note("    y/N at the terminal first and is marked unmistakably once active; declining")
    _note("    changes nothing. This is a VIEW switch (which deployment the console answers")
    _note("    about), not an engine switch -- the running agent keeps its own pair.")
    _row("  The session banner (on every screen)")
    _note("    The two header lines: the active deployment, then the market session + clock")
    _note("    for its venue. 24/7 venues (crypto) say so; session-bound venues (equities)")
    _note("    show OPEN or CLOSED with the recorded NEXT OPEN / NEXT CLOSE; CLOCK")
    _note("    UNAVAILABLE means the recorded clock is absent or stale and is rendered")
    _note("    fail-loud on purpose. All of it comes from what the agent cycle RECORDED --")
    _note("    the same session state `fetch --check` and `keel status` read -- never from a")
    _note("    clock call or calendar of the TUI's own.")
    lines.append(_blank())
    _row("Live balance")
    _note("  'live account' shows the REAL account's spendable quote balance (e.g. USDC),")
    _note("  refreshed every ~30s and immediately on 'r' or 'f' -- so a deposit or sell shows up.")
    _note("  Each refresh is a LIVE call to the venue (get_accounts) -- one of the three network")
    _note("  touches this dashboard makes, and the only one that happens without a keypress. It")
    _note("  is a read: it places no orders and changes nothing.")
    _note("  In paper mode, paper buys spend paper_cash_usdc instead -- not this balance.")
    lines.append(_blank())
    _row("Glossary (the field names the dashboard prints verbatim)")
    _row("  cycle")
    _note("    One pass of the agent loop: poll the feed, evaluate every rule against every")
    _note("    allowlisted product, decide. This deployment runs ONE cycle per day. A cycle that")
    _note("    happened and found nothing is the NORMAL case, not a fault.")
    _row("  signal")
    _note("    A rule's setup that passed the engine's gates. `signals=0` means no rule found a")
    _note("    setup at all -- which is NOT the same as a setup being found and then vetoed.")
    _row("  sig / blk / ent / exi / err")
    _note("    The activity overlay's per-cycle columns: signals, blocked (rail vetoes),")
    _note("    entered, exited, errors. `sig 1 blk 1` means keel DID find something and a rail")
    _note("    stopped it; `sig 0` means it found nothing to stop. Read the two together: they")
    _note("    are the difference between 'no setup' and 'setup, declined'.")
    _row("  paper_cash_usdc")
    _note("    The synthetic cash balance paper buys spend -- seeded once, then tracked in the")
    _note("    DB. It is NOT a real broker balance, and it appears only in paper mode.")
    _row("  equity_state_mode")
    _note("    Whether the equity figures above describe the PAPER account or the LIVE one. The")
    _note("    two are separate accounts with separate histories; neither reflects the other.")
    _row("  high_water_mark / drawdown / rail11")
    _note("    The peak equity the drawdown breaker measures against, how far equity has fallen")
    _note("    from that peak now, and whether the breaker is holding trading. The ceilings in")
    _note("    parentheses on the drawdown line come from config.")
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
    _row("Insights overlay (i)")
    _note("  Read-only, like the whole dashboard: per-rule track record, distance to the")
    _note("  promotion gate, an account summary, and a compact recent-trades tail -- the same")
    _note("  scrolling keys as help mode (up/k, down/j, PgUp/PgDn, Home/End).")
    _row("  q / Esc / i    close insights, back to the dashboard")
    lines.append(_blank())
    _row("Screen overlay (s)")
    _note("  OFFLINE, read-only: runs the current allowlist through the SAME admission gate")
    _note("  `keel assets screen` uses (`_screen_product`) -- ADMIT/REJECT per product, plus WHY.")
    _note("  DB reads only; never builds a broker, never touches the network.")
    _row("  q / Esc / s    close screen, back to the dashboard")
    lines.append(_blank())
    _row("Propose overlay (p)")
    _note("  OFFLINE, read-only: screens the newest *.json shortlist file in config.proposals_dir")
    _note("  (produced externally -- an LLM + web-search scout, or the discover overlay's output")
    _note("  saved to disk) through the same admission gate. No shortlist yet is reported plainly,")
    _note("  not as an error. DB + local filesystem reads only; never touches the network.")
    _row("  q / Esc / p    close propose, back to the dashboard")
    lines.append(_blank())
    _row("Discover overlay (d)")
    _note("  Opens ARMED, NOT yet run -- pressing 'd' makes NO network call. It explains what")
    _note("  running it will do and that it is a LIVE call to the venue. Only Enter, pressed")
    _note("  INSIDE this overlay, actually contacts the venue (`list_products`) and proposes")
    _note("  candidates from the result -- the same cheap pre-filter `keel assets discover` runs.")
    _note("  The result is then HELD: every poll while the overlay stays open repaints the same")
    _note("  cached result, with NO further network calls, until Enter is pressed again. Closing")
    _note("  the overlay discards the held result, so reopening it is armed-but-not-run again.")
    _row("  Enter          run discover now (the ONE network call this overlay ever makes)")
    _row("  q / Esc / d    close discover, back to the dashboard (discards the held result)")
    lines.append(_blank())
    _row("Activity overlay (v)")
    _note("  OFFLINE, read-only: a chronological feed of what the agent has actually been doing,")
    _note("  newest first, ONE ROW PER ENGINE CYCLE, grouped by the cycle_id every event carries.")
    _note("  Every other overlay here reports STATE -- and state is what looks dead when nothing")
    _note("  trades, because a deployment that ran flawlessly for three weeks and correctly")
    _note("  declined every setup shows the same zeroes as one that died on day one. This shows")
    _note("  the narrative instead. A QUIET cycle still gets a row: the run of quiet cycles IS")
    _note("  the answer to 'is it alive'.")
    _note("  SCOPED TO TODAY by default -- the local calendar day, midnight to now, in the same")
    _note("  clock the rows are stamped in. 'What has keel been doing' means today unless you")
    _note("  ask otherwise; t cycles the scope today -> 7 days -> all, and the scope goes back")
    _note("  to today every time the overlay is reopened (a widened view is never remembered).")
    _note("  When today holds no cycle yet -- the normal state of a once-a-day deployment every")
    _note("  morning before its run -- the panel is NOT blank: it says keel has not run yet")
    _note("  today, names when the last cycle was and when the next one is due, and tells you")
    _note("  which key widens the window. A quiet cycle that DID run is still a row.")
    _note("  If the bounded read cannot prove it reached back to midnight, the footer says so,")
    _note("  rather than letting an unread morning read as a quiet one.")
    _note("  Each row: local time, mode, and sig/blk/ent/exi/err (signals, blocked, entered,")
    _note("  exited, errors), then what was notable -- a rail veto and its rule, the gate that")
    _note("  rejected a setup, the reason an entry was not placed. Colour follows the same")
    _note("  convention as the rest of the dashboard: quiet is muted, withheld/vetoed is a")
    _note("  warning, a real fill is green, and an ERROR-level run is an alert.")
    _note("  Source: the structured JSONL engine log at `logging.file` in config.yaml (default")
    _note("  `logs/keel.log`, resolved against the WORKING DIRECTORY -- so run keel tui from the")
    _note("  deployment root). Not the database: a new table would start empty on exactly the")
    _note("  deployment this feed exists to explain, while the log is already months deep.")
    _note("  Reading a local file is neither a broker nor the network, so this overlay keeps the")
    _note("  same offline guarantee s and p do -- the three network exceptions above are still")
    _note("  the only three.")
    _note("  The read is BOUNDED (newest 1 MiB / 5000 lines / 200 cycles) so a log that grows for")
    _note("  months can never slow the dashboard down; the feed says when the bound bit.")
    _note("  A missing, empty, unreadable or unparseable log is explained in plain words -- never")
    _note("  a traceback, and never a blank screen.")
    _row("  up / k         select the previous (newer) cycle")
    _row("  down / j       select the next (older) cycle")
    _row("  Enter / Space  expand or collapse the selected cycle's events")
    _row("  t              cycle the scope: today -> last 7 days -> all history in the window")
    _row("  PgUp / PgDn / Home / End    move the selection by a page, or to either end")
    _row("  q / Esc / v    close activity, back to the dashboard (scope resets to today)")
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
    _note("  screen, propose and activity are fully OFFLINE: DB reads, plus (for propose and")
    _note("  activity) local filesystem reads. None constructs a broker or touches the network.")
    lines.append(_blank())
    _note(
        "  discover is the THIRD deliberate network exception in this dashboard. The other two"
    )
    _note("  are the automatic ~30s live-balance refresh above and [f] fetch -- three in total,")
    _note("  and nothing else here ever leaves this machine. Discover never fires on opening the")
    _note("  overlay and never fires again on its own while the overlay stays open -- only an")
    _note("  explicit Enter, pressed inside it, runs the one live venue call it ever makes.")
    lines.append(_blank())
    _note(
        "  NONE of screen, propose or discover attests, admits, or trades. They can only PROPOSE"
    )
    _note(
        "  or REPORT -- putting an asset on `allowlist` in config.yaml still needs a human to run"
    )
    _note(
        "  `keel assets attest` with a source, or the console's Compliance attest form. That form"
    )
    _note(
        "  (and the scout browser's `a` step) never attests on a keypress alone: it ends by"
    )
    _note(
        "  making you TYPE THE ASSET CODE back, and withdrawals attest types its own CLI phrase"
    )
    _note(
        "  ('yes') -- the typed phrase is the safety, never where you invoked it from. attest is"
    )
    _note(
        "  the one step in this whole gate that rests on human judgment, not code -- the form"
    )
    _note("  collects it, it never supplies it.")
    lines.append(_blank())
    _row("Every action shows a one-line result at the bottom of the dashboard until the next")
    _row("action replaces it.")
    lines.append(_blank())
    _row("Press q, Esc, h or ? now to return to the dashboard.")
    return lines


def _insights_line_style(text: str) -> str:
    """Style a single rendered line from `keel.commands.insights.render_summary`/`render_journal`
    (plain, unstyled `str`s -- that module has no notion of `ScreenLine`) by its own textual
    conventions, so the overlay reads at a glance exactly like the rest of the dashboard: a
    passing gate is reassuring green, a blocked one and its reasons are a warning, the
    small-sample caveat and "nothing yet" placeholders are muted, never alarming."""
    stripped = text.strip()
    if stripped.startswith("gate: PASSING"):
        return "ok"
    if stripped.startswith("gate: blocked"):
        return "warn"
    if stripped.startswith("- "):
        return "warn"
    if stripped.startswith(_SMALL_SAMPLE_NOTE_PREFIX):
        return "muted"
    lowered = stripped.lower()
    if "no rule track record yet" in lowered or "no closed trades yet" in lowered:
        return "muted"
    if stripped.startswith("rail11") and "HALTED" in stripped:
        return "alert"
    return "normal"


#: The first few words of `keel.commands.insights._SMALL_SAMPLE_NOTE` -- matched as a prefix
#: rather than importing the full constant (which would defeat the point of the lazy import used
#: to avoid the `insights` <-> `tui` circular import).
_SMALL_SAMPLE_NOTE_PREFIX = "n<30:"

#: How many of the most recent trades the insights overlay's optional journal tail shows --
#: compact by design (the overlay is meant to be skimmed, not to replace `keel insights journal`).
_INSIGHTS_JOURNAL_TAIL = 5


def build_insights_screen(
    insights_report: InsightsReport, journal_report: JournalReport | None = None
) -> list[ScreenLine]:
    """A titled, scrollable, READ-ONLY overlay: the per-rule track record + promotion-gate
    distance + account summary (`render_summary`), plus an optional compact recent-journal tail
    (`render_journal`) when `journal_report` is supplied. PURE -- both inputs are already-built
    reports (`build_insights_report`/`build_journal_report`, called by the live loop, never by
    this function), so this never touches the repo/network/broker itself; it only styles text
    that `keel/commands/insights.py` already rendered, exactly the way `build_screen` only styles
    `StatusReport`. Never raises on a zero-rule/zero-trade report -- `render_summary`'s own
    friendly "no rule track record yet" line covers that, so this never renders a blank overlay.
    """
    from keel.commands.insights import render_journal, render_summary

    lines: list[ScreenLine] = [ScreenLine("keel tui -- insights", "heading"), _blank()]
    for text in render_summary(insights_report):
        lines.append(ScreenLine(text, _insights_line_style(text)) if text else _blank())

    if journal_report is not None:
        lines.append(_blank())
        lines.append(ScreenLine(f"recent trades (last {_INSIGHTS_JOURNAL_TAIL}):", "heading"))
        for text in render_journal(journal_report):
            lines.append(ScreenLine(text, _insights_line_style(text)) if text else _blank())

    lines.append(_blank())
    lines.append(ScreenLine("Press i or Esc to return to the dashboard.", "muted"))
    return lines


def _admission_line_style(text: str) -> str:
    """Style a single rendered line from `keel.commands.admission`'s renderers
    (`render_screen_report`/`render_propose_view`/`render_discover_report` -- plain, unstyled
    `str`s, exactly the shape `_insights_line_style` already keys off) by the textual conventions
    those renderers already share with `keel assets screen`/`propose`/`discover`'s own CLI output.

    `ADMIT`/`REJECT` are the screen's actual verdict, so they carry the strongest legible
    contrast: reassuring green for an admit, a warning (never `"alert"` -- a reject is the system
    working as intended, not an emergency) for a reject. A `✗ ` line is a real, FAILED admission
    criterion -- `"warn"`. An `INVALID` line (`render_propose_view`'s malformed-shortlist-entry
    report) is a data problem in a file on disk, not a live threat -- also `"warn"`, not `"alert"`.

    The `! no local history` line -- and its MISSING-DATA continuation line from
    `missing_history_lines` -- are deliberately `"muted"`, NOT `"warn"`/`"alert"`, even though the
    first starts with the same `!` marker every other warning does. `keel.compliance.screen.
    split_failures`'s entire reason for existing is that "never fetched" is not a verdict about
    the asset (see `render_screen_report`'s own docstring: a candidate this deployment has simply
    never fetched candles for must not read as indistinguishable from one genuinely too young) --
    painting it in the same colour as a real rejection reason would visually assert the opposite
    of what the text says. Every OTHER `! ` line is a genuine warning (`ScreenResult.warnings`,
    e.g. a §65.5 bay' al-sarf note that applies even to an ADMITted asset) and stays `"warn"`.

    `render_discover_report`'s closing `⚠️  These are PROPOSALS, not admissions` line is the one
    line in this whole workflow that must never be missed -- discover is the network-touching
    overlay, and every candidate it lists is unvetted -- so it is `"alert"`, the same weight
    `_message_style` gives to arming autonomy ON."""
    stripped = text.strip()
    if stripped.startswith("ADMIT"):
        return "ok"
    if stripped.startswith("REJECT"):
        return "warn"
    if stripped.startswith("✗"):
        return "warn"
    if stripped.startswith("!"):
        if "no local history" in stripped.lower():
            return "muted"
        return "warn"
    if "missing-data" in stripped.lower():
        return "muted"
    if stripped.startswith("INVALID"):
        return "warn"
    if stripped.startswith("⚠"):
        return "alert"
    return "normal"


def build_admission_screen_overlay(report: ScreenReport) -> list[ScreenLine]:
    """A titled, scrollable, READ-ONLY overlay over an already-built `ScreenReport` -- PURE,
    mirroring `build_insights_screen` exactly: the caller (`run_live`'s `screen` branch, via
    `_do_screen_report`) does the OFFLINE work of building the report fresh each poll; this
    function only styles the lines `render_screen_report` already rendered. Never touches the
    repo, network, or broker itself, and never admits, attests, or trades -- see the module
    docstring."""
    lines: list[ScreenLine] = [ScreenLine("keel tui -- screen", "heading"), _blank()]
    for text in render_screen_report(report):
        lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    lines.append(_blank())
    lines.append(ScreenLine("Press s or Esc to return to the dashboard.", "muted"))
    return lines


def build_propose_overlay(view: ProposeView) -> list[ScreenLine]:
    """Same shape as `build_admission_screen_overlay`, over an already-built `ProposeView`
    (which itself NEVER raises -- every failure mode, a missing directory through a malformed
    shortlist file, is already a calm `status`/`detail` pair; see its own docstring).
    PURE -- only styles what `render_propose_view` already rendered."""
    lines: list[ScreenLine] = [ScreenLine("keel tui -- propose", "heading"), _blank()]
    for text in render_propose_view(view):
        lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    lines.append(_blank())
    lines.append(ScreenLine("Press p or Esc to return to the dashboard.", "muted"))
    return lines


#: Named once so the ARMED explanation's own text and the actual keypress `run_live`'s discover
#: branch checks for can't silently drift apart -- a mismatch here (the overlay says one key,
#: the loop listens for another) would be worse than almost anywhere else in this module, since
#: the whole point of the ARMED state is that the operator can trust what it says before it ever
#: touches the network.
_DISCOVER_RUN_KEY_HINT = "Enter"


def build_discover_overlay(
    report: DiscoverReport | None, error: str | None = None
) -> list[ScreenLine]:
    """A titled, scrollable overlay over `keel.commands.admission.build_discover_report` -- PURE,
    but unlike `build_admission_screen_overlay`/`build_propose_overlay` it renders THREE distinct
    states, not one, because `discover` is the one overlay of this trio that needs the network
    (see the module docstring and `run_live`'s discover branch for the full gating story):

    - `report is None and error is None`: **ARMED, not yet run.** This is the state the overlay
      opens into on `d` -- no network call has happened yet, and this rendering is the proof of
      that: it names what pressing `_DISCOVER_RUN_KEY_HINT` will do (fetch the venue's product
      list and propose candidates from it, the same cheap pre-filter `keel assets discover`
      runs), that it is a LIVE call to the venue, and which key runs it. A test asserting this
      state renders (rather than, say, a blank or "loading" screen) is the test that proves
      opening the overlay alone never touches the network.
    - `error is not None`: the last Enter's fetch failed (broker construction, auth, a network
      error) -- rendered as one readable line, never a raw traceback, with the same key hint so
      the operator knows how to retry.
    - `report is not None`: the HELD result of the last successful Enter, rendered via
      `render_discover_report` exactly like the other two overlays reuse their own renderer.

    Whichever state, `report`/`error` are furnished by the caller -- this function itself never
    fetches, never re-fetches, and never decides staleness; it only styles whatever it is handed.
    """
    lines: list[ScreenLine] = [ScreenLine("keel tui -- discover", "heading"), _blank()]
    if error is not None:
        lines.append(ScreenLine(f"discover failed: {error}", "alert"))
        lines.append(_blank())
        lines.append(
            ScreenLine(f"Press {_DISCOVER_RUN_KEY_HINT} to contact the venue again.", "normal")
        )
    elif report is None:
        lines.append(ScreenLine("ARMED -- no network call has been made yet.", "normal"))
        lines.append(_blank())
        lines.append(
            ScreenLine(
                f"Pressing {_DISCOVER_RUN_KEY_HINT} makes ONE live call to the venue "
                "(list_products) and proposes candidates from it -- the same cheap pre-filter "
                "`keel assets discover` runs, on the exact same data.",
                "normal",
            )
        )
        lines.append(
            ScreenLine(
                "This is the third deliberate network exception in this dashboard -- the other "
                "two are the automatic ~30s live-balance refresh and [f] fetch. It never fires "
                "just from opening this overlay, and it never fires again on its own while this "
                "overlay stays open.",
                "normal",
            )
        )
        lines.append(_blank())
        lines.append(ScreenLine("Nothing here is admitted -- discover only proposes.", "muted"))
        lines.append(_blank())
        lines.append(
            ScreenLine(f"Press {_DISCOVER_RUN_KEY_HINT} to contact the venue now.", "normal")
        )
    else:
        for text in render_discover_report(report):
            lines.append(ScreenLine(text, _admission_line_style(text)) if text else _blank())
    lines.append(_blank())
    lines.append(ScreenLine("Press d or Esc to return to the dashboard.", "muted"))
    return lines


def _activity_lines(
    feed: ActivityFeed, *, cursor: int = 0, expanded: frozenset[str] = frozenset()
) -> tuple[list[ScreenLine], int]:
    """The activity overlay's lines, PLUS the index of the line the cursor currently sits on.

    Returning both from ONE function is deliberate, and is why `build_activity_overlay` is a thin
    wrapper over this rather than the other way round. This overlay is the only one of the six
    with a *cursor* -- the others scroll a fixed body, but a feed of collapsible rows needs a
    selected row to collapse. Keeping the cursor's screen position as a separate function would
    mean a second copy of the layout arithmetic ("a row is 1 line, plus one per event when
    expanded, plus a note line when events were dropped"), and the two copies would drift the
    first time the layout changed -- with the symptom being a cursor that scrolls to the wrong
    row, which is exactly the class of bug that is invisible in a unit test of either half alone.

    PURE: `feed` is already built by the caller (`run_live`, via `build_activity_feed`), and this
    only styles what `keel.commands.activity`'s renderers already rendered -- the same discipline
    `build_insights_screen` and `build_admission_screen_overlay` keep toward their own modules.
    Never raises, and never returns an empty body: a broken log renders `describe_status`'s
    explanation, which is the whole point (a blank overlay would look exactly like the dead
    dashboard this feature exists to disprove)."""
    lines: list[ScreenLine] = [ScreenLine("keel tui -- activity", "heading")]
    # Clamped here as well as in `run_live`, which already clamps it every poll against a feed
    # that can shrink underneath it. Belt and braces for the same reason `_visible_slice` clamps
    # its own offset: an out-of-range `cursor` would otherwise mark no row as selected while
    # `cursor_line` still reported the first row's position, and the view would scroll to a row
    # nothing appears to have selected.
    cursor = max(0, min(cursor, max(0, len(feed.cycles) - 1)))

    if feed.status != "ok":
        # No scope line here on purpose: when the FILE could not be read, "scope: today" would
        # invite an operator to press `t`, and widening a window over a log that does not exist
        # changes nothing. `describe_status` owns this screen.
        lines.append(_blank())
        for text in describe_status(feed):
            lines.append(ScreenLine(text, "warn") if text else _blank())
        lines.append(_blank())
        lines.append(ScreenLine("Press v or Esc to return to the dashboard.", "muted"))
        return lines, 0

    # WHAT is being shown, directly under the title and before what happened in it. Without this
    # line a one-row "today" view is indistinguishable from a log that only had one row in it,
    # and the `t` key that would settle the question is invisible.
    lines.append(ScreenLine(scope_headline(feed), "normal"))
    lines.append(_blank())

    # The column header belongs over columns. When the scope holds no cycle there are none, and
    # `describe_empty_scope`'s prose sits directly under the scope line instead.
    if feed.cycles:
        lines.append(ScreenLine(ACTIVITY_HEADER, "heading"))
    cursor_line = len(lines)

    if not feed.cycles:
        for text in describe_empty_scope(feed):
            lines.append(ScreenLine(text, "warn") if text else _blank())
    for index, cycle in enumerate(feed.cycles):
        is_open = cycle.key in expanded
        selected = index == cursor
        if selected:
            cursor_line = len(lines)
        lines.append(
            ScreenLine(
                # `feed.now_ts` rather than a fresh `time.time()`: every row's `age` is measured
                # against the same instant the scope boundary was, so the column cannot disagree
                # with the header above it or drift row-to-row within one repaint.
                render_cycle_row(
                    cycle, now_ts=feed.now_ts, selected=selected, expanded=is_open
                ),
                cycle_style(cycle),
            )
        )
        if not is_open:
            continue
        if cycle.events_dropped:
            lines.append(
                ScreenLine(
                    f"      ... {cycle.events_dropped} earlier event(s) in this cycle are not "
                    "retained (per-cycle cap) -- the counts above still cover all of them",
                    "muted",
                )
            )
        for ev in cycle.events:
            lines.append(ScreenLine(render_event_row(ev), event_style(ev)))

    lines.append(_blank())
    for text in footer_notes(feed):
        lines.append(ScreenLine(text, "muted"))
    lines.append(_blank())
    lines.append(
        ScreenLine(
            "up/k down/j · Enter/Space expand · t scope · PgUp/PgDn/Home/End · q/Esc/v close",
            "muted",
        )
    )
    return lines, cursor_line


def build_activity_overlay(
    feed: ActivityFeed, *, cursor: int = 0, expanded: frozenset[str] = frozenset()
) -> list[ScreenLine]:
    """The activity overlay's lines alone -- the pure, directly-testable surface, matching the
    shape of every other `build_*_overlay` in this module. `run_live` uses `_activity_lines`
    instead, because it also needs the cursor's screen position to scroll it into view."""
    return _activity_lines(feed, cursor=cursor, expanded=expanded)[0]


def _activity_cursor(
    ch: int, cursor: int, height: int, total: int, curses_mod: Any, *, banner_lines: int = 0
) -> int:
    """The new, clamped SELECTED-ROW index for a keypress in the activity overlay -- the cursor
    analogue of `_scroll_offset`, taking `curses_mod` as a parameter for the identical reasons
    (lazy `curses` import; testable against the suite's existing fake curses module).

    The same keys move it that scroll the other five overlays, on purpose: an operator should not
    have to remember that this one overlay rebound up/down. What differs is what they move -- a
    row, not a line -- because a row here can be one line or seventy, and scrolling by lines
    through an expanded cycle would make selecting the next cycle a matter of counting its
    events. `_follow_cursor` then does the scrolling, so the view still moves.

    `total` is the number of CYCLES. A page is `height - 3` rows (leaving the title, blank and
    header rows in view), floored at 1 so a two-line terminal still advances.
    `banner_lines` is the console banner's height -- the feed is PAINTED with the banner
    prepended (the same combined list `_follow_cursor` already accounts for at its call site),
    so a page must leave those rows in view too or it over-advances by exactly the banner and
    lands the selection further down than the rows the operator actually saw. `0` (the
    default) is the pre-console shape, when no binding supplied a banner."""
    if total <= 0:
        return 0
    page = max(height - 3 - banner_lines, 1)
    if ch in (curses_mod.KEY_UP, ord("k")):
        cursor -= 1
    elif ch in (curses_mod.KEY_DOWN, ord("j")):
        cursor += 1
    elif ch == curses_mod.KEY_PPAGE:
        cursor -= page
    elif ch == curses_mod.KEY_NPAGE:
        cursor += page
    elif ch == curses_mod.KEY_HOME:
        cursor = 0
    elif ch == curses_mod.KEY_END:
        cursor = total - 1
    return max(0, min(cursor, total - 1))


def _follow_cursor(offset: int, cursor_line: int, height: int) -> int:
    """The smallest change to `offset` that brings `cursor_line` back into a `height`-line
    window -- scroll up to it if it is above, down to it if it is below, leave the view exactly
    where it is otherwise. PURE, and total: a zero or negative `height` (a terminal mid-resize)
    returns the offset unchanged rather than dividing by anything.

    "Smallest change" is the behaviour that matters: recentring on every keypress would make the
    whole feed jump under the operator's eyes each time they moved one row, which is precisely
    what makes a scrolling list unreadable."""
    if height <= 0:
        return max(0, offset)
    if cursor_line < offset:
        return max(0, cursor_line)
    if cursor_line >= offset + height:
        return max(0, cursor_line - height + 1)
    return max(0, offset)


def _cursor_line_index(lines: list[ScreenLine]) -> int:
    """The index of the single `>`-marked cursor row in `lines`, 0 when there is none --
    the input to `_follow_cursor`'s scroll math for the cursor-driven console lists (the
    Compliance menu, the scout file list), whose builders mark the selected row the same
    way. Scanning beats duplicating each builder's header height here: the two can never
    disagree about where the cursor is. PURE."""
    for index, line in enumerate(lines):
        if line.text.startswith(">"):
            return index
    return 0


def _visible_slice(lines: list[ScreenLine], offset: int, height: int) -> list[ScreenLine]:
    """The `height`-line window of `lines` starting at `offset`, clamped so `offset` never runs
    past what would leave a partial screen at the end (or before the start). PURE -- never raises
    on a tiny/zero `height` or an `offset` far past the end of `lines`."""
    if height <= 0:
        return []
    max_offset = max(0, len(lines) - height)
    offset = max(0, min(offset, max_offset))
    return lines[offset : offset + height]


def _scroll_offset(ch: int, offset: int, height: int, total: int, curses_mod: Any) -> int:
    """The new, clamped scroll offset for a keypress inside any of the five scrollable overlays
    (help, insights, screen, propose, discover). Factored out of `run_live` because its help and
    insights branches used to each hand-roll an identical ~8-line up/down/PgUp/PgDn/Home/End
    chain -- copy-pasting that a further three times for the new overlays, onto a function that
    was already long, would have made it worse rather than better.

    PURE: takes `curses_mod` as a parameter rather than importing `curses` itself, for two
    reasons that both matter here -- `curses` is imported lazily inside `run_live` (this module
    must stay importable with no real terminal present, and the pure-function tests must stay
    portable), and passing it in is what lets this function be unit-tested against the SAME fake
    `curses` module the rest of the `run_live` test suite already builds, with no real terminal
    or `curses.wrapper` involved.

    `total` is the number of lines in the list being SCROLLED, banner included when one is
    prepended (`len(banner) + len(overlay_lines)` at every call site in `run_live`): the list
    this offset is applied to is the same combined list `_visible_slice` slices, so the clamp
    here and the window there must agree about its length -- a banner-excluded total left `End`
    short of the true last page by exactly the banner's height. Used exactly the way
    `help_offset`/`insights_offset` always were: `End` jumps toward the bottom (clamped, like
    every other result, to the last full page) and every key's result is clamped to `[0, max(0,
    total - height)]` so the view can never scroll past either end."""
    if ch in (curses_mod.KEY_UP, ord("k")):
        offset -= 1
    elif ch in (curses_mod.KEY_DOWN, ord("j")):
        offset += 1
    elif ch == curses_mod.KEY_PPAGE:
        offset -= max(height - 1, 1)
    elif ch == curses_mod.KEY_NPAGE:
        offset += max(height - 1, 1)
    elif ch == curses_mod.KEY_HOME:
        offset = 0
    elif ch == curses_mod.KEY_END:
        offset = total
    return max(0, min(offset, max(0, total - height)))


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

#: The `r` toast. `r` re-reads local state and forces the live-balance re-fetch, but it used to
#: set no message at all -- and since the DB usually has not changed between two repaints, the
#: screen came back byte-identical and the key was indistinguishable from a dead one. Every other
#: action key toasts; this says what `r` did, and (unlike `f`) what it deliberately did NOT do.
#: The balance is re-read AFTER the paint, so it lands on the following frame -- hence "balance
#: updating" rather than a claim it is already showing.
_REFRESH_MESSAGE = "refreshed local state -- balance updating (no candle fetch; use [f] for that)"

#: How often (seconds) `run_live` refreshes the live "available to buy" balance -- deliberately
#: SLOWER than the dashboard's own repaint interval (typically 5s): it is a real broker call
#: (`get_accounts`), and re-fetching it every repaint would hammer the venue for no operator
#: benefit. `r` (refresh-now) and a completed `f` (fetch) both force an immediate re-fetch by
#: resetting the cadence clock, so the balance still updates promptly on demand.
_BALANCE_REFRESH_SEC = 30

#: Network timeout (seconds) bounding the live balance's `get_accounts` call -- a hung request
#: must never freeze the whole dashboard indefinitely. Deliberately generous (this is a
#: background refresh, not something the operator is blocked waiting on) but finite.
_BALANCE_TIMEOUT_SEC = 10

#: Network timeout (seconds) bounding the discover overlay's one `list_products` call. TIGHTER
#: than `_BALANCE_TIMEOUT_SEC`'s rationale would suggest is needed, because the operator is
#: BLOCKED on this one: they pressed Enter, the screen is frozen behind a "contacting venue"
#: frame, and there is no other feedback until the call returns. A hung venue must become a
#: readable `discover failed:` line they can retry, never an indefinitely frozen dashboard whose
#: only exit is Ctrl-C (which kills the whole TUI, not just the request).
_DISCOVER_TIMEOUT_SEC = 20


def _refresh_balance(
    open_state: OpenState, now_fn: NowFn, balance_fn: Callable[[Config], Decimal | None]
) -> AvailableBalance:
    """Fetch the live "available to buy" balance -- injectable (`open_state`/`now_fn`/
    `balance_fn`) so it's testable with fakes, no curses/network/broker involved. FAIL-SOFT: any
    exception anywhere (opening the repo, reading config, the broker call inside `balance_fn`)
    becomes an error `AvailableBalance` with `quote="?"` (the configured quote currency itself may
    be unreadable), never a raised exception -- a transient balance-read failure must never crash
    the live loop. `balance_fn(config) -> Decimal | None` returning `None` covers BOTH "no matching
    account" and any broker/auth/network error it swallowed internally, so the message deliberately
    does not assert "no balance" -- that would wrongly tell an operator a deposit never landed when
    the real cause could be an unreachable broker or bad credentials."""
    try:
        _repo, config = open_state()
        amount = balance_fn(config)
        if amount is None:
            error = (
                f"{config.quote_currency} balance unreadable "
                "(no funds, or account/credentials unavailable)"
            )
            return AvailableBalance(None, config.quote_currency, now_fn(), error)
        return AvailableBalance(amount, config.quote_currency, now_fn(), None)
    except Exception as exc:
        # Truncated: this raw exception text is painted full-screen (`_available_lines`) -- keep
        # a stray huge or sensitive blob (e.g. an HTTP error body) off the display.
        error = str(exc)[:120]
        return AvailableBalance(None, "?", now_fn(), error)


def run_once(
    open_state: OpenState,
    now_fn: NowFn,
    echo: Echo,
    banner_fn: Callable[[Any, Any, int], list[ScreenLine]] | None = None,
) -> None:
    """Render a single frame and hand each line to `echo` -- drives `--once` (pipes/CI) and is
    directly testable with fakes, no CliRunner or terminal needed.

    `banner_fn(repo, config, now_ts)` (v6, the console shell) optionally prepends the
    session banner's lines to the frame -- the same header every interactive screen
    carries, so a piped snapshot names its deployment and market session too. Keyword-only
    and defaulted so every existing caller renders exactly as before."""
    repo, config = open_state()
    now_ts = now_fn()
    if banner_fn is not None:
        for line in banner_fn(repo, config, now_ts):
            echo(line.text)
    report = gather_status(repo, config, now_ts)
    for text in render_plain(report, now_ts):
        echo(text)


def _message_style(message: str) -> str:
    """`"alert"` for a message that reports a failure, arms autonomy ON, or switches the
    console to the LIVE deployment -- the transitions that must never read as reassuring
    green, because real money is what they change (a green `profile -> LIVE` would be the
    toast colour saying all is well about real-account data starting to answer from every
    screen); `"warn"` for a cancelled/unchanged action; `"ok"` for everything else (autonomy
    OFF, fetch complete, a PAPER profile switch) -- purely cosmetic, so a quick glance at the
    toast's colour tells you which it was."""
    lowered = message.lower()
    if (
        "-> on" in lowered
        or "without asking" in lowered
        or "failed" in lowered
        or "-> live" in lowered
    ):
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


def _confirm_live_profile(stdscr: Any, profile: Any) -> bool:
    """The Profile menu's LIVE guard (issue #388 C2): suspends curses and asks an explicit
    y/N at the terminal -- the same suspend/restore dance `_confirm_arm_autonomy` keeps,
    over `click.confirm` rather than the typed-word gate.

    Deliberately a confirm STEP, not O3's typed contract: pointing the console at the live
    deployment changes what the operator is LOOKING at, not what the engine does (the
    running agent keeps its own pair), so the ceremony matches `click.confirm`'s, not
    `resume`'s. Fails CLOSED -- any exception anywhere here (including while restoring the
    screen, or a Ctrl-C/EOF out of `click.confirm`) returns `False` and the binding stays
    exactly where it was."""
    import curses

    try:
        curses.def_prog_mode()
        curses.endwin()
        try:
            return click.confirm(
                f"Switch the console to the LIVE deployment "
                f"({profile.config_path} + {profile.db_path})? "
                "Real-account data answers from here on",
                default=False,
            )
        finally:
            curses.reset_prog_mode()
            stdscr.refresh()
    except Exception:
        return False


def _do_fetch(open_state: OpenState, now_fn: NowFn) -> str:
    """Fetch fresh candle history for every allowlisted product, money-safe (data only, never
    places an order). Lazy-imports the fetch primitives from `keel.data.history`/
    `keel.commands._common`/`keel.commands._products` to avoid a `cli` <-> `tui` import cycle at
    module load time. Thin I/O -- not unit-tested directly, only smoke-tested via `run_live`."""
    from keel.commands._common import _build_broker
    from keel.commands._products import _default_sim_products
    from keel.data import history as history_mod

    repo, config = open_state()
    products = _default_sim_products(config)
    client = _build_broker(config)
    now_ts = now_fn()
    years = 5  # matches `keel fetch --years`'s own default
    # The same config-driven list `keel fetch` warms (Issue #349): this is the dashboard twin
    # of THAT command, not of `simulate`, so the engine's ONE_HOUR/ONE_DAY limit does not
    # apply -- the FIFTEEN_MINUTE confirmation series must warm here too.
    granularities = list(config.market_data.granularities)
    history_mod.ensure_history(
        client,
        repo,
        products,
        granularities,
        years,
        now_ts,
        sleep_fn=time.sleep,
    )
    return f"fetch complete ({len(products)} products, {years}y history)"


def _do_screen_report(open_state: OpenState) -> ScreenReport:
    """Build a fresh `ScreenReport` for the current allowlist -- OFFLINE, DB reads only, rebuilt
    every poll while the screen overlay is open, exactly like insights' per-poll rebuild.
    `screen_product` (THE single admission gate every candidate source must route through) lives
    in `keel.commands.assets` (issue #387 C1) -- the shared service layer, importable here with
    no `keel.cli` cycle and no lazy-import dodge: one gate, two front-ends.
    Thin I/O -- not unit-tested directly, only smoke-tested via `run_live`."""
    from keel.commands.assets import screen_product

    repo, config = open_state()
    return build_screen_report(repo, config, screen_product)


def _do_compliance_payload(open_state: OpenState, now_fn: NowFn, kind: str) -> Any:
    """Gather the payload one Compliance view renders (issue #389 C3): the OFFLINE kinds
    only -- a service report, rebuilt each poll, fail-soft in the caller. The two
    network-gated kinds (holdings/discover) are NOT gathered here; they go through
    `_do_compliance_network` behind an explicit Enter, the discover overlay's own gating
    story. Thin I/O -- every branch dispatches to a service function; nothing is computed
    here."""
    from keel.commands import compliance_console

    if kind == "screen":
        return _do_screen_report(open_state)
    if kind == "propose":
        return _do_propose_view(open_state)
    repo, config = open_state()
    if kind == "purification":
        from keel.commands.purification import render_purification_report
        from keel.compliance.purification import build_report

        return render_purification_report(build_report(repo.get_transactions()))
    if kind == "subscription":
        from keel.commands.subscription import subscription_show_lines

        return subscription_show_lines(repo, config, now_fn())
    if kind == "shariah":
        from keel.commands.assets import gather_attestations_in_force
        from keel.execution.executor import _withdrawals_enabled

        inventory = gather_attestations_in_force(repo, config)
        return compliance_console.build_shariah_lines(
            inventory, withdrawals_enabled=_withdrawals_enabled(repo, now_fn()), now_ts=now_fn()
        )
    raise ValueError(f"unknown offline compliance view: {kind}")


def _do_compliance_network(open_state: OpenState, kind: str) -> Any:
    """The ONE live call each network-gated Compliance view makes, run only from an
    explicit Enter (issue #389 C3): `get_accounts` for holdings (the same balance read
    the dashboard's live-balance line makes) or `list_products` for discover (the same
    product-metadata read `_do_discover_report` makes). Both are BOUNDED, for the same
    reason `_refresh_balance`/`_do_discover_report` are: the operator is actively waiting
    on a frozen screen. Thin I/O -- the report compute is the service's.

    The discover payload is `assets.run_discovery(client, products, config)` -- the SAME
    sweep `keel assets discover` runs, because the view renders it through
    `assets.render_discover` (`DiscoverSweep`'s own renderer). `admission.
    build_discover_report`'s `DiscoverReport` is NOT that shape: it used to be handed to
    `render_discover` here and the successful-Enter frame died on an `AttributeError`
    (`DiscoverReport has no survivor_count`) -- the ARMED and error frames hid it, because
    only a successful Enter ever reached the render."""
    from keel.commands._common import _build_broker

    if kind == "discover":
        from keel.commands.assets import run_discovery

        _repo, config = open_state()
        client = _build_broker(config, timeout=_DISCOVER_TIMEOUT_SEC)
        return run_discovery(client, client.list_products(), config)
    if kind == "holdings":
        from keel.commands.assets import gather_holdings

        repo, config = open_state()
        # min-balance 0 and no screen: `keel assets holdings`'s own defaults.
        accounts = _build_broker(config, timeout=_BALANCE_TIMEOUT_SEC).get_accounts()
        return gather_holdings(repo, config, accounts, Decimal("0"))
    raise ValueError(f"unknown network compliance view: {kind}")


def _run_terminal_form(stdscr: Any, fn: Callable[[], str]) -> str:
    """Run one Compliance form at the TERMINAL, inside the console session: suspend
    curses (the same `def_prog_mode` -> `endwin` -> `reset_prog_mode` dance
    `_confirm_arm_autonomy`/`_confirm_live_profile` keep), let the form's prompts and
    O3's typed gates render in-console, restore the screen, and return the form's result
    line (the confirmation of what was written). A form that cannot run at all (an
    exception outside its own fail-soft handling) becomes a readable toast, never a
    crash of the loop."""
    import curses

    try:
        curses.def_prog_mode()
        curses.endwin()
        try:
            return fn()
        finally:
            curses.reset_prog_mode()
            stdscr.refresh()
    except Exception as exc:
        return f"form failed: {exc}"[:200]


def _do_propose_view(open_state: OpenState) -> ProposeView:
    """Build a fresh `ProposeView` over the newest shortlist in `config.proposals_dir` -- OFFLINE
    (DB + local filesystem reads only), rebuilt every poll while the propose overlay is open.
    `screen_product` comes from `keel.commands.assets` for the identical reason
    `_do_screen_report` names: the shared gate, not a `keel.cli` import.

    `build_propose_view` is fail-soft about the shortlist FILE -- a missing directory, a
    permissions error, a non-UTF-8 file, invalid JSON, a malformed top-level shape all come back
    as a `status`/`detail` pair. That is not the same as "this function cannot raise", and the
    earlier claim that any exception here could only come from `open_state()` was simply wrong:
    it once let a `UnicodeDecodeError` from the read itself through (that specific hole is now
    closed -- see `build_propose_view`'s own `except (OSError, UnicodeDecodeError)`), and
    `build_propose_view` still SCREENS every parsed candidate afterwards, which means real DB
    reads through `screen_product`. A locked DB, mid-screen, surfaces here exactly like one from
    `open_state()` does. The caller's `try/except` is load-bearing for both."""
    from keel.commands.assets import screen_product

    repo, config = open_state()
    return build_propose_view(repo, config, screen_product)


def cached_scout_view(
    repo: Any,
    config: Any,
    screen_fn: Any,
    path: Path,
    cache: dict[tuple[str, int], ProposeView],
) -> ProposeView:
    """`build_propose_view` for the scout browser, cached per (path, mtime_ns): the scout
    view repaints every poll, and re-reading, re-parsing and re-SCREENING the same
    unchanged file through the admission gate each poll (a DB read per candidate per
    poll, and an unbounded read besides -- `build_propose_view` now bounds it at 1 MiB)
    is pure waste. A file whose mtime changed (or could not be stat'd -- it is rebuilt,
    fail-soft, rather than served stale) refreshes. The cache is the CALLER'S dict, single
    purpose: one shortlist is open at a time, and the caller clears it when a write makes
    the screening stale (the `a` attest step) so a fresh attestation re-screens at once.

    PURE over its inputs (the filesystem aside); `build_propose_view` itself never raises
    for file problems, so an exception here is a repo problem the caller already fail-softs."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = -1  # unstatable: a key that can never be re-hit, so it never caches
    key = (str(path), mtime_ns)
    if key in cache:
        return cache[key]
    view = build_propose_view(repo, config, screen_fn, path=path)
    cache.clear()  # single entry: only the open shortlist is worth holding
    cache[key] = view
    return view


def _do_discover_report(open_state: OpenState) -> DiscoverReport:
    """THE one network call anywhere in the discover overlay -- `_build_broker(config).
    list_products()` -- followed by the PURE `build_discover_report`, which turns the venue's raw
    product list into candidates. Called ONLY from `run_live`'s discover branch, and only from
    its Enter-key handler: never on opening the overlay, never on an ordinary poll while it stays
    open. `_build_broker` is lazy-imported from `keel.commands._common`, mirroring `_do_fetch`'s
    own lazy broker import, so a test can monkeypatch `keel.commands._common._build_broker` (to
    record calls, or to raise if called at all) and prove this function -- and therefore this
    whole overlay -- was, or crucially was NOT, ever invoked. Thin I/O -- not unit-tested
    directly, only smoke-tested via `run_live`.

    BOUNDED by `_DISCOVER_TIMEOUT_SEC`, for the reason `_refresh_balance` is bounded and `_do_fetch`
    deliberately is not: this is a single, small metadata request the operator is actively waiting
    on with the screen frozen behind a "contacting venue" frame, so a hung connection must fail and
    say so rather than freeze the dashboard until Ctrl-C. (`f` fetch legitimately runs for minutes
    pulling 5y of candles, which is why it is documented as freezing the dashboard instead of being
    given a timeout that would abort honest work.)"""
    from keel.commands._common import _build_broker

    _repo, config = open_state()
    products = _build_broker(config, timeout=_DISCOVER_TIMEOUT_SEC).list_products()
    return build_discover_report(products, config)


def run_live(
    open_state: OpenState,
    now_fn: NowFn,
    interval: float,
    console_binding: Any | None = None,
) -> None:
    """The auto-refreshing, interactive dashboard: `curses.wrapper` a loop that re-opens the repo
    (via `open_state`) every poll -- so it reflects writes committed by a separate `keel agent`
    process -- gathers a fresh report, paints it, then waits up to `interval` seconds for a
    keypress.

    Ten modes when `console_binding` is supplied (v6, issue #388 C2 -- the console shell),
    the seven below plus `menu` (the PRD §3 tree over `console.build_menu_lines`), `profile`
    (the deployment menu over `console.build_profile_menu_lines`, switching through
    `console.switch_profile` with `_confirm_live_profile` guarding the LIVE pair) and
    `placeholder` (a future slice's "lands in Cx" notice). EVERY screen -- all ten -- is
    prepended the session banner (`console.console_banner_lines`, fail-soft), and `m` in
    normal mode opens the menu. `console_binding is None` is the pre-C2 dashboard
    byte-for-byte: no banner, no `m`, none of the new modes -- which is what every caller
    that predates the shell (and every pre-existing test) passes.

    C3 (issue #389) adds the Compliance sub-tree's four modes, all console-bound only:
    `compliance` (the sub-menu; a form entry runs its form at the TERMINAL through
    `_run_terminal_form` -- curses suspended, O3's typed gates rendering in-console -- and
    the write's confirmation line toasts on the menu), `compliance-view` (one service
    report overlay; the offline kinds rebuild per poll fail-soft, the network kinds --
    holdings/discover -- open ARMED and hold what an explicit Enter fetched),
    `scout-list`/`scout-view` (the scout-results browser, O6: list the config-named
    proposals directory, screen the chosen shortlist through the admission services, and
    offer -- never auto-run -- the TYPED attest step for a selected candidate).

    C4 (issue #390) adds the strategy console and the research readers, all
    console-bound only: `strategy` (the Rules sub-menu over
    `strategy_console.STRATEGY_MENU`; forms run at the terminal like Compliance's),
    `strategy-ledger` (the tried-vs-used ledger -- every rule row with its RECORDED
    context, read CHEAPLY once on entry: zero backtests run there, because the
    entry-time re-backtest this view shipped with cost minutes per rule on long
    series), `strategy-rule` (one rule's detail: params rendered through
    `describe_params`, the O8 per-field help, and the rule's backtest verdict -- ARMED
    until an explicit Enter, the ONE place the strategy console runs a rule backtest:
    warned first, honestly blocking while it runs like simulate/fetch, held in
    `strategy_verdicts` and rendered under its row once computed),
    `strategy-simulate` (the O11.1 ARMED view -- Enter is the confirm
    step; the run fetches/writes exactly as `keel simulate` does, blocks the loop exactly
    like `f` fetch, and its verdict+report render under a pinned footer), and the four
    `research*` modes (the O5 evidence readers over `research_console`: a corpus list, a
    bounded mtime-cached document view, and the trials ledger with its chain verdict --
    all read-only). The strategy menu's `insights` entry opens the pre-existing insights
    overlay with a BACK-POINTER so it closes onto the Rules menu, not the dashboard.

    Seven modes: `normal` (the dashboard, plus a transient one-line `message` toast from the last
    action), `help` (a scrolled window of `build_help_screen()`), `insights` (a scrolled window of
    `build_insights_screen()` -- a READ-ONLY overlay over `build_insights_report`/
    `build_journal_report`, rebuilt fresh each poll while open, fail-soft exactly like the
    normal-mode status read below), `screen` and `propose` (the OFFLINE admission-workflow
    overlays, `build_admission_screen_overlay`/`build_propose_overlay` over `_do_screen_report`/
    `_do_propose_view`, rebuilt fresh each poll while open, fail-soft the same way insights is),
    `discover` (the one overlay that touches the network -- see below), and `activity` (the
    chronological, one-row-per-cycle feed over the engine LOG rather than the DB -- also OFFLINE,
    also rebuilt each poll, and also fail-soft, though `build_activity_feed` is itself total so
    the handler's `try` only ever catches `open_state()` failing). Five of the six scrollable
    overlays share one scrolling helper, `_scroll_offset`; `activity` is the exception, driving
    `_activity_cursor` + `_follow_cursor` instead because its up/down keys move a SELECTED ROW
    (which may be one line or seventy) rather than one line of a fixed body. The console's
    `menu`/`profile` modes move a selected row the same way `activity` does, and `placeholder`
    does not scroll (a notice is one screen).

    Normal-mode keys: `q`/`Q` quit; `h`/`?` open help; `i` open insights; `s` open screen; `p`
    open propose; `d` open discover; `v` open activity; `m` open the console menu (when a
    console binding was supplied); `r` refresh now; `a` toggle autonomy
    (`toggle_autonomy`,
    gated by `_confirm_arm_autonomy` on the OFF->ON direction only); `f` fetch all data
    (`_do_fetch`, money-safe). `a` and `f` are both wrapped in `_guarded` so a failure becomes a
    toast, never a crash. help/insights/screen/propose all scroll (`up`/`k`, `down`/`j`,
    `PgUp`/`PgDn`, `Home`/`End`) and close back to normal on `q`/`Esc`/<their own open key>.
    `activity` binds the same keys to moving its selected row, and adds Enter/Space to expand or
    collapse that row's cycle into its individual events, plus `t` to cycle the day scope
    (`today` -> `7d` -> `all`). `t` was free: `q Q h ? i r a f s p d v m` are the dashboard's keys,
    `k`/`j`/Enter/Space the in-overlay ones, and nothing bound `t` anywhere. The scope is reset to
    `today` both when `v` opens the overlay and when any close key leaves it, so it can never
    become sticky across visits.

    `discover` is different on purpose, and is the whole reason this docstring calls out the
    overlays individually rather than treating them identically: it needs the network, and that
    network call
    must never fire just from pressing `d`. Opening it renders an ARMED, not-yet-run explanation
    (`build_discover_overlay(None)`) with NO call made. Only Enter (`10`/`13`/`curses.KEY_ENTER`),
    pressed INSIDE the overlay, runs `_do_discover_report` -- the one
    `_build_broker(config).list_products()` call in this entire workflow. The result (or error)
    is then HELD in `discover_result`/`discover_error`: every later poll while the overlay stays
    open just repaints what is held, with NO further network calls, until Enter is pressed again.
    Closing the overlay (`q`/`Esc`/`d`) discards the held result, so reopening it is armed but
    not yet run again. `discover` still scrolls with the same keys as the other four.

    The console shell adds NO network touch of its own: the banner reads the repo and the
    adapter's offline capabilities declaration, profile switching reads local files, and a
    switch RESETS the held live-balance line (it described the previous venue's account).

    Also refreshes the live "available to buy" balance (`_refresh_balance`) on its own slow
    cadence (`_BALANCE_REFRESH_SEC`, not every repaint -- it's a real broker call), and
    immediately on `r`. Between that automatic slow-cadence read, `f` fetch, and now `d`+Enter,
    those are the only three places this whole dashboard ever touches the network -- everything
    else, including all of `screen`/`propose`/`discover`'s own reads, is DB/filesystem-only.

    C3's Compliance menu adds two more EXPLICITLY-GATED network touches, both behind their
    own Enter inside an ARMED view (never on open, never on a poll): the holdings view's
    one `get_accounts` (the same read the slow-cadence balance line makes) and the discover
    view's one `list_products` (the same read `d`+Enter makes, reached from the menu).
    Both are bounded (`_BALANCE_TIMEOUT_SEC`/`_DISCOVER_TIMEOUT_SEC`) and both hold their
    result until re-Enter or close, the discover overlay's own story.

    Quits on `q`/`Q` in normal mode; a `KeyboardInterrupt` (Ctrl-C) exits gracefully rather than
    dumping a traceback onto a terminal `curses.wrapper` may not have fully restored."""
    import curses

    # Lazy import, the established cycle-dodge for this module (see `insights`): console
    # imports THIS module at load time (its builders speak `ScreenLine`), so importing it
    # here keeps the two modules loadable in either order. The strategy/research consoles
    # follow the same rule (both speak `ScreenLine` and dispatch to the service layer).
    from keel.commands import compliance_console, console, research_console, strategy_console

    def _balance_fn(cfg: Config) -> Decimal | None:
        # Lazy imports -- `keel.commands._common` and `keel.execution.executor` both import
        # (transitively) from `keel.cli`/`keel.commands`, so importing them at module load time
        # would create an import cycle with this module. `_fetch_available_quote` is the EXACT
        # live-balance read rail 13 funds a buy against, reused verbatim so the TUI and the rail
        # never disagree.
        from keel.commands._common import _build_broker
        from keel.execution.executor import _fetch_available_quote

        return _fetch_available_quote(
            _build_broker(cfg, timeout=_BALANCE_TIMEOUT_SEC), cfg.quote_currency
        )

    def _loop(stdscr: Any) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            # Not every terminal has a hideable cursor -- never fatal to the dashboard.
            pass

        def _console_banner() -> list[ScreenLine]:
            """The session banner for the screen about to be painted -- O9's per-screen
            header, read fresh each poll from the RECORDED state. Empty when no console
            binding was supplied (the pre-C2 dashboard, unchanged), and FAIL-SOFT with a
            warn line when the read itself fails: a banner read must never take the screen
            it heads down with it."""
            if console_binding is None:
                return []
            try:
                banner_repo, banner_config = open_state()
                return console.console_banner_lines(
                    console_binding, banner_repo, banner_config, now_fn()
                )
            except Exception as exc:
                return [ScreenLine(f"console header read failed: {exc}", "warn")]

        def _enter_menu_entry(
            action: str, entry: console.MenuEntry
        ) -> tuple[str, int, int, console.MenuEntry | None]:
            """Where a menu selection goes: a closed mapping in one place, so the rendered
            tree, the ordinals and the dispatch can never disagree about what an entry
            does. Every destination opens at the top (cursor 0, offset 0)."""
            if action == "dashboard":
                return "normal", 0, 0, None
            if action == "profile":
                return "profile", 0, 0, None
            if action == "help":
                return "help", 0, 0, None
            if action == "compliance":
                return "compliance", 0, 0, None
            if action == "strategy":
                return "strategy", 0, 0, None
            if action == "research":
                return "research", 0, 0, None
            return "placeholder", 0, 0, entry

        mode = "normal"
        help_offset = 0
        insights_offset = 0
        screen_offset = 0
        propose_offset = 0
        discover_offset = 0
        activity_offset = 0
        activity_cursor = 0
        menu_cursor = 0
        profile_cursor = 0
        placeholder_entry: console.MenuEntry | None = None
        # ALWAYS reset to `today` on open (below), never carried across one -- see this module's
        # docstring. A widened scope answers a question the operator asked once; making it sticky
        # would quietly turn "what is keel doing" back into "here is a fortnight of scrollback".
        activity_scope = DEFAULT_ACTIVITY_SCOPE
        # Keyed by `ActivityCycle.key`, NOT by row index: the feed is rebuilt from the log every
        # poll, so a new cycle appearing at the top would silently shift every index down one and
        # expand the wrong row. The key is stable across rebuilds, so an expanded cycle stays
        # expanded even as the feed grows underneath it.
        activity_expanded: frozenset[str] = frozenset()
        discover_result: DiscoverReport | None = None
        discover_error: str | None = None
        # -- the Compliance menu (issue #389 C3): sub-menu cursor, the open view's kind,
        # its held network result (holdings/discover open ARMED and hold what Enter ran),
        # and the scout browser's listing/selection state.
        compliance_cursor = 0
        compliance_offset = 0
        compliance_view_kind: str | None = None
        compliance_result: Any = None
        compliance_error: str | None = None
        scout_files: tuple[Any, ...] = ()
        scout_dir: Any = None
        scout_cursor = 0
        scout_selected: Any = None
        scout_view_offset = 0
        scout_candidate_cursor = 0
        # The menu's own scroll offset (the sub-menu is 15 entries -- more rows than a
        # small terminal once every description fits the 80-column budget), the scout
        # LIST's (older shortlists sit below the fold on any but a huge window), and the
        # scout view's per-(path, mtime) cache (`cached_scout_view` -- repaints do not
        # re-screen an unchanged file).
        compliance_menu_offset = 0
        scout_list_offset = 0
        scout_view_cache: dict[tuple[str, int], ProposeView] = {}
        # -- the strategy console (issue #390 C4 / PRD O11): the Rules sub-menu's cursor,
        # the ledger's HELD rows (built CHEAPLY once per entry -- recorded state only,
        # ZERO backtests; the entry-time re-backtest this view shipped with cost minutes
        # per rule on long series), the per-rule verdicts the operator EXPLICITLY
        # re-computes (Enter-gated in the rule's detail; held here and dropped with the
        # rows when the ledger is re-entered -- the rules-table-write invalidation), the
        # simulate view's ARMED/held state (Enter is the confirm step, the discover
        # overlay's gating story), and the research readers' list/selection state.
        strategy_cursor = 0
        strategy_menu_offset = 0
        strategy_ledger: list[Any] = []
        strategy_ledger_built = False
        strategy_ledger_cursor = 0
        strategy_ledger_offset = 0
        strategy_rule_detail: Any = None
        strategy_rule_offset = 0
        strategy_verdicts: dict[int, Any] = {}
        strategy_simulate_plan: Any = None
        strategy_simulate_result: Any = None
        strategy_simulate_error: str | None = None
        strategy_simulate_progress: list[str] = []
        strategy_simulate_offset = 0
        # -- the research readers (issue #390 C4 / PRD O5)
        research_cursor = 0
        research_menu_offset = 0
        research_files: tuple[Any, ...] = ()
        research_dir: Any = None
        research_title = ""
        research_list_cursor = 0
        research_list_offset = 0
        research_doc_path: Any = None
        research_doc_cache: dict[tuple[str, int], list[str]] = {}
        research_doc_offset = 0
        research_trials_offset = 0
        # Where the insights overlay closes back to: the dashboard by default, the Rules
        # menu when the strategy console opened it (the shell is a hierarchy).
        insights_back = "normal"
        message: str | None = None
        message_ts = 0
        available: AvailableBalance | None = None
        last_balance_ts = 0

        def _toast_ttl() -> str | None:
            """The current toast, if it has not aged out -- the compliance screens render
            it too (a write's confirmation belongs on the screen it was invoked from)."""
            if message is not None and now_fn() - message_ts <= _MESSAGE_TTL_SEC:
                return message
            return None

        def _form_prompt(text: str) -> str:
            """One form field, asked at the terminal while curses is suspended -- the
            prompt side of every Compliance form (the typed gates run their own
            `click.prompt` inside `_require_interactive_confirmation`, unchanged)."""
            return click.prompt(text, default="", show_default=False)

        def _run_form_at_terminal(form_target: str) -> None:
            """A record-write entry: open the state through the console's own loaders and
            run the form through `compliance_console.run_form` -- THE dispatch seam, the
            same function the unit tests drive -- inside the suspend/restore dance. The
            form's result line (confirmation or cancellation) becomes the toast."""
            nonlocal message, message_ts

            form_repo, form_config = open_state()
            message = _run_terminal_form(
                stdscr,
                lambda: compliance_console.run_form(
                    form_target, form_repo, form_config, _form_prompt, now_fn()
                ),
            )
            message_ts = now_fn()

        def _enter_compliance_entry(entry: compliance_console.ComplianceEntry) -> None:
            """Where a Compliance selection goes -- the same closed-mapping rule
            `_enter_menu_entry` keeps, for the sub-tree: a view (the network-gated ones
            open ARMED), the scout browser, or a form run right here at the terminal."""
            nonlocal mode, compliance_view_kind, compliance_result, compliance_error
            nonlocal compliance_offset, scout_files, scout_dir, scout_cursor
            nonlocal scout_selected, scout_view_offset, scout_candidate_cursor
            nonlocal scout_list_offset
            if entry.kind == "view":
                compliance_view_kind = entry.target
                compliance_result = None
                compliance_error = None
                compliance_offset = 0
                mode = "compliance-view"
            elif entry.kind == "scout":
                _scout_repo, scout_config = open_state()
                scout_files, scout_dir = compliance_console.scout_listing(scout_config)
                scout_cursor = 0
                scout_list_offset = 0
                scout_selected = None
                mode = "scout-list"
            else:
                _run_form_at_terminal(entry.target)

        def _run_strategy_form_at_terminal(form_target: str) -> None:
            """A strategy-console form entry: open the state through the console's own
            loaders and run the form through `strategy_console.run_*` inside the
            suspend/restore dance -- the same seam the Compliance forms use, so the O3
            typed gates (the retry `--force` phrase) render in-console."""
            nonlocal message, message_ts

            form_repo, form_config = open_state()

            def _run() -> str:
                if form_target == "add":
                    return strategy_console.run_add_form(
                        form_repo, form_config, _form_prompt, now_fn()
                    )
                if form_target == "retry":
                    return strategy_console.run_retry_form(
                        form_repo, form_config, _form_prompt, now_fn()
                    )
                if form_target == "enable":
                    return strategy_console.run_enable_form(
                        form_repo, form_config, _form_prompt, now_fn()
                    )
                if form_target == "disable":
                    return strategy_console.run_disable_form(
                        form_repo, form_config, _form_prompt, now_fn()
                    )
                if form_target == "demote":
                    return strategy_console.run_demote_form(
                        form_repo, form_config, _form_prompt, now_fn()
                    )
                raise ValueError(f"unknown strategy form: {form_target}")

            message = _run_terminal_form(stdscr, _run)
            message_ts = now_fn()

        def _enter_strategy_entry(entry: Any) -> None:
            """Where a Rules selection goes -- the closed-mapping rule again: the ledger
            (a CHEAP recorded-state read -- no backtest runs on entry; the per-rule
            verdict is the detail view's explicit Enter-gated re-compute), the ARMED
            simulate view, a form at the terminal, or the insights overlay with a
            back-pointer to this menu."""
            nonlocal mode, strategy_ledger, strategy_ledger_built, strategy_ledger_cursor
            nonlocal strategy_ledger_offset, strategy_rule_detail, strategy_rule_offset
            nonlocal strategy_verdicts
            nonlocal strategy_simulate_plan, strategy_simulate_result
            nonlocal strategy_simulate_error, strategy_simulate_progress
            nonlocal strategy_simulate_offset, insights_back
            nonlocal message, message_ts, insights_offset
            if entry.kind == "view":  # the tried-vs-used ledger
                try:
                    ledger_repo, ledger_config = open_state()
                    strategy_ledger = strategy_console.build_strategy_ledger(
                        ledger_repo, ledger_config, now_fn()
                    )
                except Exception as exc:
                    strategy_ledger = []
                    message = f"ledger read failed: {str(exc)[:160]}"
                    message_ts = now_fn()
                    return
                # Re-entering rebuilds the rows from the rules table AND drops every held
                # verdict -- a rules-table write between visits can never keep a stale
                # verdict on a changed row.
                strategy_verdicts = {}
                strategy_ledger_built = True
                strategy_ledger_cursor = 0
                strategy_ledger_offset = 0
                strategy_rule_detail = None
                mode = "strategy-ledger"
            elif entry.kind == "armed":  # simulate: opens ARMED, Enter confirms and runs
                sim_repo, sim_config = open_state()
                strategy_simulate_plan = strategy_console.simulate_plan(
                    sim_config, console_binding.db_path if console_binding else "?",
                    now_ts=now_fn(),
                )
                strategy_simulate_result = None
                strategy_simulate_error = None
                strategy_simulate_progress = []
                strategy_simulate_offset = 0
                mode = "strategy-simulate"
            elif entry.kind == "insights":
                insights_back = "strategy"
                mode = "insights"
                insights_offset = 0
            else:
                _run_strategy_form_at_terminal(entry.target)

        def _enter_research_entry(entry: Any) -> None:
            """Where a Research selection goes: a corpus list (read on entry, the scout
            browser's contract) or the trials-ledger view."""
            nonlocal mode, research_files, research_dir, research_title
            nonlocal research_list_cursor, research_list_offset, research_trials_offset
            if entry.kind == "trials":
                research_trials_offset = 0
                mode = "research-trials"
            else:
                research_title = entry.target
                research_dir = research_console.corpus_path(entry.target)
                research_files = research_console.list_documents(research_dir)
                research_list_cursor = 0
                research_list_offset = 0
                mode = "research-list"

        while True:
            if mode == "menu":
                if console_binding is None:  # unreachable via 'm'; kept total anyway
                    mode = "normal"
                    continue
                # The console menu (v6): the PRD §3 tree, one cursor-marked row, ordinals
                # 1-9 as direct shortcuts. Not scrolled -- the nine entries plus the banner
                # fit the terminals this dashboard has always targeted, and `_paint` clips
                # a tiny window harmlessly.
                profiles = console.discover_profiles()
                menu_lines = console.build_menu_lines(
                    console.active_profile(
                        console_binding.config_path, console_binding.db_path, profiles
                    ),
                    cursor=menu_cursor,
                    profiles=profiles,
                )
                _paint(stdscr, [*_console_banner(), *menu_lines])
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "normal"
                elif ch in (curses.KEY_UP, ord("k")):
                    menu_cursor = max(0, menu_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    menu_cursor = min(len(console.CONSOLE_MENU) - 1, menu_cursor + 1)
                elif ord("1") <= ch <= ord("9"):
                    selected = console.menu_entry(ch - ord("0"))
                    if selected is not None:
                        mode, menu_cursor, help_offset, placeholder_entry = _enter_menu_entry(
                            selected.action, selected
                        )
                        if selected.action == "compliance":
                            # Enter the sub-menu at the top, like every other destination.
                            compliance_menu_offset = 0
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER):
                    selected = console.CONSOLE_MENU[menu_cursor]
                    mode, menu_cursor, help_offset, placeholder_entry = _enter_menu_entry(
                        selected.action, selected
                    )
                    if selected.action == "compliance":
                        compliance_menu_offset = 0
                continue

            if mode == "placeholder":
                if console_binding is None or placeholder_entry is None:  # unreachable; total
                    mode = "normal"
                    continue
                # A future slice's entry: the notice names the owning slice and says the
                # shell renders navigation only. Any close key returns to the MENU (the
                # parent screen), not the dashboard -- the shell is a hierarchy.
                _paint(
                    stdscr,
                    [
                        *_console_banner(),
                        *console.build_placeholder_lines(placeholder_entry),
                    ],
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "menu"
                continue

            if mode == "profile":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # The Profile menu (O4): every discovered deployment with its config+db
                # pair visible, LIVE marked as guarded. Selecting rebinds through the same
                # loaders the CLI uses (`console.switch_profile` -> `ConsoleBinding.
                # open_state`), guarded by `_confirm_live_profile` for the LIVE pair only.
                profiles = console.discover_profiles()
                profile_lines = console.build_profile_menu_lines(
                    profiles, cursor=profile_cursor, binding_pair=console_binding.pair
                )
                _paint(stdscr, [*_console_banner(), *profile_lines])
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("p"), ord("m")):
                    # `m` closes profile mode too -- the same q/Esc/<open-key-or-m>
                    # consistency the menu and placeholder modes keep, so the key that
                    # opened the shell can always step back one level out of it.
                    mode = "menu"
                elif ch in (curses.KEY_UP, ord("k")):
                    profile_cursor = max(0, profile_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    profile_cursor = profile_cursor + 1
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER) and 0 <= profile_cursor < len(
                    profiles
                ):
                    selected_profile = profiles[profile_cursor]
                    before_pair = console_binding.pair
                    message = _guarded(
                        "profile switch",
                        lambda: console.switch_profile(
                            console_binding,
                            selected_profile,
                            confirm_fn=lambda: _confirm_live_profile(stdscr, selected_profile),
                        ),
                    )
                    message_ts = now_fn()
                    if console_binding.pair != before_pair:
                        # The whole console now answers about the other deployment: back
                        # to the landing screen so the rebinding is visible everywhere,
                        # the live-balance line dropped (it described the previous
                        # venue's account) and re-read on the next poll.
                        mode = "normal"
                        profile_cursor = 0
                        available = None
                        last_balance_ts = 0
                # Clamp every poll: the discovered list can change under the cursor (a
                # config file appearing/disappearing between polls).
                profile_cursor = max(0, min(profile_cursor, max(0, len(profiles) - 1)))
                continue

            if mode == "compliance":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # The Compliance sub-menu (issue #389 C3): PRD §3's Compliance branch,
                # one cursor-marked row, the typed entries marked. A FORM entry runs at
                # the terminal right here (curses suspended) and its confirmation line
                # becomes the toast on this screen; views and the scout browser are
                # separate modes that close back HERE -- the shell is a hierarchy.
                # SCROLLED like the other cursor-driven lists (`_visible_slice` +
                # `_follow_cursor`): the tree is 15 entries and no longer fits a small
                # window one-row-per-entry, so the cursor's row must follow the cursor.
                compliance_lines = compliance_console.build_compliance_menu_lines(
                    cursor=compliance_cursor, message=_toast_ttl()
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(compliance_lines)
                compliance_menu_offset = _follow_cursor(
                    compliance_menu_offset, cursor_row, height
                )
                _paint(
                    stdscr,
                    _visible_slice(
                        [*banner, *compliance_lines], compliance_menu_offset, height
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "menu"
                elif ch in (curses.KEY_UP, ord("k")):
                    compliance_cursor = max(0, compliance_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    compliance_cursor = min(
                        len(compliance_console.COMPLIANCE_MENU) - 1, compliance_cursor + 1
                    )
                elif ord("1") <= ch <= ord("9"):
                    compliance_selected = compliance_console.compliance_entry(ch - ord("0"))
                    if compliance_selected is not None:
                        _enter_compliance_entry(compliance_selected)
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER):
                    _enter_compliance_entry(compliance_console.COMPLIANCE_MENU[compliance_cursor])
                else:
                    # Banner-aware total, for the same reason as help's branch above.
                    compliance_menu_offset = _scroll_offset(
                        ch,
                        compliance_menu_offset,
                        height,
                        len(banner) + len(compliance_lines),
                        curses,
                    )
                continue

            if mode == "compliance-view" and compliance_view_kind is not None:
                # One service-report overlay, FAIL-SOFT like the screen/propose branches:
                # the offline kinds rebuild each poll inside the try (a locked DB paints
                # an alert line and keeps polling); the network kinds (holdings/discover)
                # do NOT rebuild -- `compliance_result`/`compliance_error` are HELD from
                # the last Enter (ARMED until one is pressed), the discover overlay's own
                # gating story, so a poll can never fire a venue call.
                if compliance_view_kind in ("holdings", "discover"):
                    view_lines = compliance_console.build_compliance_view_lines(
                        compliance_view_kind, compliance_result, error=compliance_error
                    )
                else:
                    try:
                        view_lines = compliance_console.build_compliance_view_lines(
                            compliance_view_kind,
                            _do_compliance_payload(open_state, now_fn, compliance_view_kind),
                        )
                    except Exception as exc:
                        view_lines = compliance_console.build_compliance_view_lines(
                            compliance_view_kind, None, error=str(exc)[:200]
                        )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                # The shariah browser's two standing honesty lines are a FIXED footer,
                # reserved off the window BEFORE the body is sliced (`pinned_frame`) --
                # O10's "always visible" holds at EVERY scroll offset, where riding the
                # body's tail left them one viewport below the fold on a real allowlist.
                pinned = (
                    compliance_console.shariah_honesty_lines()
                    if compliance_view_kind == "shariah"
                    else []
                )
                _paint(
                    stdscr,
                    compliance_console.pinned_frame(
                        [*banner, *view_lines], pinned,
                        offset=compliance_offset, height=height,
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("c")):
                    mode = "compliance"
                    compliance_view_kind = None
                    compliance_offset = 0
                    # Closing discards a held network result -- reopening is armed again.
                    compliance_result = None
                    compliance_error = None
                elif (
                    compliance_view_kind in ("holdings", "discover")
                    and ch in (10, 13, curses.KEY_ENTER)
                ):
                    _paint(stdscr, [ScreenLine("contacting venue... please wait", "normal")])
                    try:
                        compliance_result = _do_compliance_network(
                            open_state, compliance_view_kind
                        )
                        compliance_error = None
                    except Exception as exc:
                        compliance_result = None
                        compliance_error = str(exc)[:200]
                    compliance_offset = 0
                else:
                    # Banner-aware total, for the same reason as help's branch above --
                    # and the scroll math spends only the rows the pinned footer leaves.
                    compliance_offset = _scroll_offset(
                        ch,
                        compliance_offset,
                        max(height - len(pinned), 0),
                        len(banner) + len(view_lines),
                        curses,
                    )
                continue

            if mode == "scout-list":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # The scout-results browser's file list (O6): every shortlist in the
                # CONFIG-named proposals directory, newest first, with a clear empty
                # state. No network, no repo read -- a directory listing, re-read only on
                # entry (the files do not change under a held screen the way a DB does).
                # SCROLLED with the cursor-follow rule like the Compliance menu: older
                # shortlists sit below the fold on any but a huge window, and O6's whole
                # point is that they stay REACHABLE.
                scout_lines = compliance_console.build_scout_list_lines(
                    scout_files, scout_dir, cursor=scout_cursor
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(scout_lines)
                scout_list_offset = _follow_cursor(scout_list_offset, cursor_row, height)
                _paint(
                    stdscr,
                    _visible_slice([*banner, *scout_lines], scout_list_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    # `m` closes the list too -- the q/Esc/m consistency every other
                    # console mode keeps, so the key that opened the shell can always
                    # step back one level out of it.
                    mode = "compliance"
                elif ch in (curses.KEY_UP, ord("k")):
                    scout_cursor = max(0, scout_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    scout_cursor = scout_cursor + 1
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER) and 0 <= scout_cursor < len(
                    scout_files
                ):
                    scout_selected = scout_files[scout_cursor].path
                    scout_view_offset = 0
                    scout_candidate_cursor = 0
                    scout_view_cache = {}  # a fresh selection starts uncached
                    mode = "scout-view"
                else:
                    # Banner-aware total, for the same reason as help's branch above.
                    scout_list_offset = _scroll_offset(
                        ch,
                        scout_list_offset,
                        height,
                        len(banner) + len(scout_lines),
                        curses,
                    )
                scout_cursor = max(0, min(scout_cursor, max(0, len(scout_files) - 1)))
                continue

            if mode == "scout-view" and scout_selected is not None:
                # The selected shortlist, screened through THE admission services
                # (fail-soft: `build_propose_view` itself never raises for file problems;
                # this catches a locked DB), with a cursor over the candidate rows. The
                # view is cached per (path, mtime) -- the screen repaints every poll, and
                # an UNCHANGED file must not be re-read, re-parsed and re-screened each
                # time; a changed file (new mtime) refreshes, and the `a` attest step
                # clears the cache because its write changes what screening says.
                # `a` offers the TYPED attest step for the selected candidate --
                # proposer-never-decider, so nothing attests without the human's phrase.
                from keel.commands.assets import screen_product

                scout_repo, scout_config = open_state()
                try:
                    scout_view = cached_scout_view(
                        scout_repo, scout_config, screen_product, scout_selected,
                        scout_view_cache,
                    )
                except Exception as exc:
                    scout_view = ProposeView(
                        source=scout_selected,
                        status="unreadable",
                        detail=str(exc)[:200],
                        report=None,
                    )
                scout_view_lines, scout_cursor_line, scout_candidates = (
                    compliance_console.build_scout_file_lines(
                        scout_view, cursor=scout_candidate_cursor
                    )
                )
                scout_candidate_cursor = max(
                    0, min(scout_candidate_cursor, max(0, scout_candidates - 1))
                )
                toast = _toast_ttl()
                if toast is not None:
                    scout_view_lines = [
                        *scout_view_lines,
                        _blank(),
                        ScreenLine(toast, _message_style(toast)),
                    ]
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                scout_view_offset = _follow_cursor(
                    scout_view_offset, scout_cursor_line + len(banner), height
                )
                _paint(
                    stdscr,
                    _visible_slice([*banner, *scout_view_lines], scout_view_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    # `m` closes the view too -- the q/Esc/m consistency every other
                    # console mode keeps, so the key that opened the shell can always
                    # step back one level out of it.
                    mode = "scout-list"
                    scout_view_offset = 0
                elif ch == ord("a") and scout_candidates > 0:
                    # `scout_candidates > 0` implies an "ok" view with a report
                    # (`build_scout_file_lines` counts `report.screened`); the assert
                    # states that invariant for the type checker, not the runtime.
                    assert scout_view.report is not None
                    selected_candidate = scout_view.report.screened[
                        scout_candidate_cursor
                    ].candidate.asset
                    message = _run_terminal_form(
                        stdscr,
                        lambda: compliance_console.run_attest_form(
                            scout_repo,
                            _form_prompt,
                            now_fn(),
                            asset=selected_candidate,
                        ),
                    )
                    message_ts = now_fn()
                    # The write changes what the gate says (an attested candidate no
                    # longer reads REJECT) -- the cached view is stale the moment it
                    # lands, so the next poll re-screens.
                    scout_view_cache = {}
                elif ch in (curses.KEY_UP, ord("k")):
                    scout_candidate_cursor = max(0, scout_candidate_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    if scout_candidates:
                        scout_candidate_cursor = min(
                            scout_candidates - 1, scout_candidate_cursor + 1
                        )
                else:
                    scout_view_offset = _scroll_offset(
                        ch,
                        scout_view_offset,
                        height,
                        len(banner) + len(scout_view_lines),
                        curses,
                    )
                continue

            if mode == "strategy":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # The Rules sub-menu (issue #390 C4): the strategy console. A FORM entry
                # runs at the terminal right here (curses suspended -- the retry flow's
                # TYPED --force gate renders in-console) and its confirmation lines toast
                # on this screen; the ledger, simulate and insights are separate modes
                # that close back HERE. SCROLLED with the cursor-follow rule: eight
                # entries with wrapped descriptions outgrow a small window.
                strategy_lines = strategy_console.build_strategy_menu_lines(
                    cursor=strategy_cursor, message=_toast_ttl()
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(strategy_lines)
                strategy_menu_offset = _follow_cursor(
                    strategy_menu_offset, cursor_row, height
                )
                _paint(
                    stdscr,
                    _visible_slice(
                        [*banner, *strategy_lines], strategy_menu_offset, height
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "menu"
                elif ch in (curses.KEY_UP, ord("k")):
                    strategy_cursor = max(0, strategy_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    strategy_cursor = min(
                        len(strategy_console.STRATEGY_MENU) - 1, strategy_cursor + 1
                    )
                elif ord("1") <= ch <= ord("9"):
                    strategy_selected = strategy_console.strategy_entry(ch - ord("0"))
                    if strategy_selected is not None:
                        _enter_strategy_entry(strategy_selected)
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER):
                    _enter_strategy_entry(
                        strategy_console.STRATEGY_MENU[strategy_cursor]
                    )
                else:
                    strategy_menu_offset = _scroll_offset(
                        ch,
                        strategy_menu_offset,
                        height,
                        len(banner) + len(strategy_lines),
                        curses,
                    )
                continue

            if mode == "strategy-ledger":
                if console_binding is None or not strategy_ledger_built:
                    mode = "strategy"
                    continue
                # The tried-vs-used ledger (O11.2): every rule row with its RECORDED
                # context, HELD from the entry-time read (a poll repaint must never
                # re-read, and never backtests -- entry runs zero of them). Any verdicts
                # the operator re-computed in the detail view render under their rows.
                # A cursor over the rule rows; Enter opens the detail (whose own Enter
                # is the explicit, warned re-compute).
                ledger_lines = strategy_console.build_ledger_lines(
                    strategy_ledger,
                    cursor=strategy_ledger_cursor,
                    verdicts=strategy_verdicts,
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(ledger_lines)
                strategy_ledger_offset = _follow_cursor(
                    strategy_ledger_offset, cursor_row, height
                )
                _paint(
                    stdscr,
                    _visible_slice(
                        [*banner, *ledger_lines], strategy_ledger_offset, height
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "strategy"
                    strategy_ledger_built = False
                elif ch in (curses.KEY_UP, ord("k")):
                    strategy_ledger_cursor = max(0, strategy_ledger_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    strategy_ledger_cursor = min(
                        max(0, len(strategy_ledger) - 1), strategy_ledger_cursor + 1
                    )
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER) and strategy_ledger:
                    strategy_rule_detail = strategy_ledger[strategy_ledger_cursor]
                    strategy_rule_offset = 0
                    mode = "strategy-rule"
                else:
                    strategy_ledger_offset = _scroll_offset(
                        ch,
                        strategy_ledger_offset,
                        height,
                        len(banner) + len(ledger_lines),
                        curses,
                    )
                continue

            if mode == "strategy-rule" and strategy_rule_detail is not None:
                # One rule's detail: the params rendered through `describe_params` (the
                # O8 per-field help), the recorded paper-gate distance, and the rule's
                # backtest verdict -- ARMED until an explicit Enter. That Enter is the
                # ONE place the strategy console runs a rule backtest: warned first (the
                # detail screen says what it costs), honestly blocking while it runs
                # (exactly like simulate/fetch), and held in `strategy_verdicts` when it
                # ends -- repaints render the held verdict, they never recompute.
                detail = strategy_rule_detail
                rule_lines = strategy_console.build_ledger_detail_lines(
                    detail, verdict=strategy_verdicts.get(detail.rule_id)
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *rule_lines], strategy_rule_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "strategy-ledger"
                    strategy_rule_offset = 0
                elif ch in (10, 13, curses.KEY_ENTER):
                    _paint(
                        stdscr,
                        [ScreenLine(
                            f"re-computing rule {detail.rule_id}'s verdict (the "
                            "full-window backtest)... please wait -- this can take "
                            "minutes on long series; the screen is frozen like "
                            "simulate/fetch",
                            "normal",
                        )],
                    )
                    try:
                        verdict_repo, verdict_config = open_state()
                        strategy_verdicts[detail.rule_id] = (
                            strategy_console.compute_rule_verdict(
                                verdict_repo, verdict_config, detail
                            )
                        )
                    except Exception as exc:
                        strategy_verdicts[detail.rule_id] = (
                            strategy_console.RuleVerdict(
                                stats_line=None,
                                reason_lines=(
                                    f"the re-compute failed before the backtest could "
                                    f"run: {str(exc)[:160]}",
                                ),
                            )
                        )
                    strategy_rule_offset = 0
                else:
                    strategy_rule_offset = _scroll_offset(
                        ch,
                        strategy_rule_offset,
                        height,
                        len(banner) + len(rule_lines),
                        curses,
                    )
                continue

            if mode == "strategy-simulate" and strategy_simulate_plan is not None:
                # The simulate view (O11.1): ARMED until an explicit Enter -- the confirm
                # step; opening the screen and polling make NO call and touch NO file.
                # Enter runs `run_simulation` (which fetches history when the cache does
                # not cover the window and WRITES the report), blocking the loop exactly
                # like `f` fetch does -- the CLI's own UX, mirrored honestly -- then the
                # result is HELD and every poll repaints it until Enter re-runs or the
                # view closes (which re-arms it).
                if strategy_simulate_result is not None:
                    sim_lines = strategy_console.build_simulate_result_lines(
                        strategy_simulate_result, tuple(strategy_simulate_progress)
                    )
                elif strategy_simulate_error is not None:
                    # A failed run keeps the progress it streamed BEFORE failing, above
                    # the error (they head the results on success; dropping them here
                    # would hide exactly the lines that say how far it got).
                    sim_lines = []
                    if strategy_simulate_progress:
                        sim_lines.append(
                            ScreenLine("run progress (what the CLI streamed):", "muted")
                        )
                        for line in strategy_simulate_progress:
                            for wrapped in textwrap.wrap(line, width=78) or [""]:
                                sim_lines.append(ScreenLine(wrapped, "muted"))
                        sim_lines.append(_blank())
                    sim_lines.extend(
                        [
                            ScreenLine(f"simulate failed: {strategy_simulate_error}", "alert"),
                            _blank(),
                            ScreenLine("Press Enter to retry, or q/Esc to close.", "muted"),
                        ]
                    )
                else:
                    sim_lines = strategy_console.build_simulate_armed_lines(
                        strategy_simulate_plan
                    )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                pinned = (
                    strategy_console.simulate_verdict_footer(strategy_simulate_result)
                    if strategy_simulate_result is not None
                    else []
                )
                _paint(
                    stdscr,
                    compliance_console.pinned_frame(
                        [*banner, *sim_lines], pinned,
                        offset=strategy_simulate_offset, height=height,
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "strategy"
                    # Closing discards the held result -- reopening is ARMED again.
                    strategy_simulate_result = None
                    strategy_simulate_error = None
                    strategy_simulate_progress = []
                    strategy_simulate_offset = 0
                elif ch in (10, 13, curses.KEY_ENTER):
                    _paint(
                        stdscr,
                        [ScreenLine(
                            "simulating... please wait (this can take minutes; the "
                            "screen is frozen exactly like the CLI)", "normal"
                        )],
                    )
                    progress: list[str] = []
                    try:
                        sim_repo, sim_config = open_state()
                        from keel.commands._common import _build_broker

                        strategy_simulate_result = strategy_console.run_simulate(
                            sim_repo,
                            sim_config,
                            strategy_simulate_plan,
                            now_ts=now_fn(),
                            # The CLI's own default: fetch history when the cache does
                            # not cover the window (a broker is constructed for it).
                            build_client=lambda: _build_broker(sim_config),
                            progress=progress,
                        )
                        strategy_simulate_error = None
                    except Exception as exc:
                        strategy_simulate_result = None
                        strategy_simulate_error = str(exc)[:200]
                    strategy_simulate_progress = progress
                    strategy_simulate_offset = 0
                else:
                    strategy_simulate_offset = _scroll_offset(
                        ch,
                        strategy_simulate_offset,
                        max(height - len(pinned), 0),
                        len(banner) + len(sim_lines),
                        curses,
                    )
                continue

            if mode == "research":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # The Research sub-menu (issue #390 C4 / O5): the evidence corpora and
                # the trials ledger, all read-only.
                research_lines = research_console.build_research_menu_lines(
                    cursor=research_cursor, message=_toast_ttl()
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(research_lines)
                research_menu_offset = _follow_cursor(
                    research_menu_offset, cursor_row, height
                )
                _paint(
                    stdscr,
                    _visible_slice(
                        [*banner, *research_lines], research_menu_offset, height
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "menu"
                elif ch in (curses.KEY_UP, ord("k")):
                    research_cursor = max(0, research_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    research_cursor = min(
                        len(research_console.RESEARCH_MENU) - 1, research_cursor + 1
                    )
                elif ord("1") <= ch <= ord("9"):
                    research_selected = research_console.research_entry(ch - ord("0"))
                    if research_selected is not None:
                        _enter_research_entry(research_selected)
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER):
                    _enter_research_entry(
                        research_console.RESEARCH_MENU[research_cursor]
                    )
                else:
                    research_menu_offset = _scroll_offset(
                        ch,
                        research_menu_offset,
                        height,
                        len(banner) + len(research_lines),
                        curses,
                    )
                continue

            if mode == "research-list":
                if console_binding is None:  # unreachable via the menu; kept total anyway
                    mode = "normal"
                    continue
                # A corpus's file list, read on ENTRY (documents do not change under a
                # held screen the way a DB does) -- the scout browser's contract.
                doc_list_lines = research_console.build_doc_list_lines(
                    research_title, research_files, research_dir,
                    cursor=research_list_cursor,
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                cursor_row = len(banner) + _cursor_line_index(doc_list_lines)
                research_list_offset = _follow_cursor(
                    research_list_offset, cursor_row, height
                )
                _paint(
                    stdscr,
                    _visible_slice(
                        [*banner, *doc_list_lines], research_list_offset, height
                    ),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "research"
                elif ch in (curses.KEY_UP, ord("k")):
                    research_list_cursor = max(0, research_list_cursor - 1)
                elif ch in (curses.KEY_DOWN, ord("j")):
                    research_list_cursor = min(
                        max(0, len(research_files) - 1), research_list_cursor + 1
                    )
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER) and research_files:
                    research_doc_path = research_files[research_list_cursor].path
                    research_doc_cache = {}  # a fresh selection starts uncached
                    research_doc_offset = 0
                    mode = "research-doc"
                else:
                    research_list_offset = _scroll_offset(
                        ch,
                        research_list_offset,
                        height,
                        len(banner) + len(doc_list_lines),
                        curses,
                    )
                continue

            if mode == "research-doc" and research_doc_path is not None:
                # The chosen document, read through the BOUNDED, mtime-cached reader --
                # repaints do not re-read an unchanged file (the scout-view lesson).
                doc_lines = research_console.build_doc_lines(
                    research_title,
                    research_doc_path,
                    research_console.cached_document_lines(
                        research_doc_path, research_doc_cache
                    ),
                )
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *doc_lines], research_doc_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "research-list"
                    research_doc_offset = 0
                else:
                    research_doc_offset = _scroll_offset(
                        ch,
                        research_doc_offset,
                        height,
                        len(banner) + len(doc_lines),
                        curses,
                    )
                continue

            if mode == "research-trials":
                # The trials ledger (O5): rebuilt per poll, fail-soft -- it is a small
                # read-only file read, and a mid-view append (a simulate run in another
                # terminal) then shows up on the next repaint.
                try:
                    trials_lines = research_console.build_trials_lines()
                except Exception as exc:
                    trials_lines = [
                        ScreenLine(f"trials read failed: {exc} -- retrying...", "alert")
                    ]
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *trials_lines], research_trials_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("m")):
                    mode = "research"
                    research_trials_offset = 0
                else:
                    research_trials_offset = _scroll_offset(
                        ch,
                        research_trials_offset,
                        height,
                        len(banner) + len(trials_lines),
                        curses,
                    )
                continue

            if mode == "help":
                help_lines = build_help_screen()
                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr, _visible_slice([*banner, *help_lines], help_offset, height)
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("h"), ord("?")):
                    mode = "normal"
                    help_offset = 0
                else:
                    # The banner is PART of the scrolled list (`_visible_slice` slices the
                    # combined `[banner, help]`), so the scroll math must count it too --
                    # clamping against the banner-EXCLUDED length left `End` two lines short
                    # and the help tail permanently hidden whenever a console binding was
                    # supplied. No binding -> the banner is empty and the total is unchanged.
                    help_offset = _scroll_offset(
                        ch, help_offset, height, len(banner) + len(help_lines), curses
                    )
                continue

            if mode == "insights":
                # Read-only + fail-soft, mirroring the normal-mode status read below: a transient
                # error (e.g. `database is locked` from a concurrent `keel agent` writer) must
                # never crash the loop -- it paints an alert line and keeps polling instead. Only
                # read methods are touched (`gather_status` is broker-free, and
                # `build_insights_report`/`build_journal_report` are pure views over it plus
                # `Repository`'s existing read methods) -- this never places an order or writes.
                from keel.commands.insights import build_insights_report, build_journal_report

                now_ts = now_fn()
                try:
                    insights_repo, insights_config = open_state()
                    insights_status = gather_status(insights_repo, insights_config, now_ts)
                    insights_report = build_insights_report(
                        insights_repo, insights_config, insights_status, now_ts
                    )
                    journal_report = build_journal_report(
                        insights_repo,
                        insights_status,
                        now_ts,
                        limit=_INSIGHTS_JOURNAL_TAIL,
                    )
                    insights_lines = build_insights_screen(insights_report, journal_report)
                except Exception as exc:
                    insights_lines = [
                        ScreenLine(f"insights read failed: {exc} -- retrying...", "alert")
                    ]

                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *insights_lines], insights_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("i")):
                    # `insights_back`: the dashboard by default, the Rules menu when the
                    # strategy console's insights entry opened it (the shell is a
                    # hierarchy).
                    mode = insights_back
                    insights_back = "normal"
                    insights_offset = 0
                else:
                    # Banner-aware total, for the same reason as help's branch above: the
                    # clamp must land on the same last page the combined slice shows.
                    insights_offset = _scroll_offset(
                        ch, insights_offset, height, len(banner) + len(insights_lines), curses
                    )
                continue

            if mode == "screen":
                # OFFLINE + fail-soft, mirroring the insights branch above: `_do_screen_report`
                # only reads the DB (never a broker, never the network -- see its own docstring),
                # and a transient read error paints an alert line and keeps polling instead of
                # crashing the loop.
                try:
                    screen_report = _do_screen_report(open_state)
                    screen_lines = build_admission_screen_overlay(screen_report)
                except Exception as exc:
                    screen_lines = [
                        ScreenLine(f"screen read failed: {exc} -- retrying...", "alert")
                    ]

                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *screen_lines], screen_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("s")):
                    mode = "normal"
                    screen_offset = 0
                else:
                    # Banner-aware total, for the same reason as help's branch above.
                    screen_offset = _scroll_offset(
                        ch, screen_offset, height, len(banner) + len(screen_lines), curses
                    )
                continue

            if mode == "propose":
                # OFFLINE + fail-soft, same shape as screen above. `build_propose_view` turns
                # every SHORTLIST-FILE problem into a calm `status`/`detail` pair rather than an
                # exception, so this handler is not what renders "no shortlist yet" or "not valid
                # JSON" -- the overlay does. It catches what is left: a locked DB, either from
                # `open_state()` or from the per-candidate screening `build_propose_view` runs
                # after parsing. It is also the net that caught a `UnicodeDecodeError` escaping
                # the read for a non-UTF-8 shortlist -- as an unreadable `'utf-8' codec can't
                # decode byte 0xff...` toast, repainted every poll, naming no file. That hole is
                # fixed at the source now; this stays as the backstop, not as the explanation.
                try:
                    propose_lines = build_propose_overlay(_do_propose_view(open_state))
                except Exception as exc:
                    propose_lines = [
                        ScreenLine(f"propose read failed: {exc} -- retrying...", "alert")
                    ]

                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *propose_lines], propose_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("p")):
                    mode = "normal"
                    propose_offset = 0
                else:
                    # Banner-aware total, for the same reason as help's branch above.
                    propose_offset = _scroll_offset(
                        ch, propose_offset, height, len(banner) + len(propose_lines), curses
                    )
                continue

            if mode == "activity":
                # OFFLINE + fail-soft, like screen/propose above -- but reading a FILE, not the
                # DB (see the module docstring for why that is admissible under this dashboard's
                # own iron rule). `build_activity_feed` is itself total: a missing, empty,
                # unreadable or unparseable log already comes back as a status this overlay
                # explains in plain words. This `try` therefore catches only what is left --
                # `open_state()` itself failing, e.g. a locked DB from a concurrent `keel agent`
                # writer -- and turns it into the same kind of readable feed rather than a crash.
                try:
                    _activity_repo, activity_config = open_state()
                    # `now_ts` comes from the SAME `now_fn` the dashboard clocks everything else
                    # with, rather than from a `time.time()` inside the feed builder: the day
                    # boundary is then a value this loop owns and a test can pin, and it can
                    # never disagree with the timestamps the rest of the screen is showing.
                    activity_feed = build_activity_feed(
                        activity_config, scope=activity_scope, now_ts=float(now_fn())
                    )
                    # Re-clamp every poll, not just on a keypress: the feed is rebuilt from a
                    # file another process is writing, so it can SHRINK between polls (a rotation
                    # empties it) and leave the cursor past the end.
                    activity_cursor = max(
                        0, min(activity_cursor, max(0, len(activity_feed.cycles) - 1))
                    )
                    activity_lines, activity_cursor_line = _activity_lines(
                        activity_feed, cursor=activity_cursor, expanded=activity_expanded
                    )
                except Exception as exc:
                    # The RENDER is inside this `try`, not just the feed build, and that is
                    # load-bearing rather than tidy: `build_activity_feed` is total, but the
                    # renderers it feeds are handed values written by another process (a `ts` a
                    # bad clock put in the year 5,000,000, say). Guarding only the build would
                    # leave the one call that actually formats those values outside the net, and
                    # an exception there would escape `curses.wrapper` and kill the dashboard.
                    activity_feed = ActivityFeed(
                        status="unreadable", source="", detail=str(exc)[:200]
                    )
                    activity_lines, activity_cursor_line = _activity_lines(activity_feed)

                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                activity_offset = _follow_cursor(
                    activity_offset, activity_cursor_line + len(banner), height
                )
                _paint(
                    stdscr, _visible_slice([*banner, *activity_lines], activity_offset, height)
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("v")):
                    mode = "normal"
                    activity_offset = 0
                    activity_cursor = 0
                    activity_scope = DEFAULT_ACTIVITY_SCOPE
                    activity_expanded = frozenset()
                elif ch in (10, 13, ord(" "), curses.KEY_ENTER):
                    if 0 <= activity_cursor < len(activity_feed.cycles):
                        activity_expanded = activity_expanded ^ {
                            activity_feed.cycles[activity_cursor].key
                        }
                elif ch == ord("t"):
                    # Widen (or wrap back to today). The cursor and scroll go back to the top
                    # because every row under them is about to change: leaving the selection on
                    # row 40 of a scope that now holds one row would land it somewhere arbitrary.
                    activity_scope = next_activity_scope(activity_scope)
                    activity_cursor = 0
                    activity_offset = 0
                else:
                    activity_cursor = _activity_cursor(
                        ch,
                        activity_cursor,
                        height,
                        len(activity_feed.cycles),
                        curses,
                        banner_lines=len(banner),
                    )
                continue

            if mode == "discover":
                # NETWORK-GATED, on purpose -- see the module docstring and `build_discover_
                # overlay`'s. Unlike every branch above, this one does NOT rebuild anything on an
                # ordinary poll: `discover_result`/`discover_error` are HELD from the last Enter
                # (both `None` if Enter has never been pressed since the overlay opened), and
                # every poll just repaints whatever is currently held. The Enter-key check below
                # is the ONLY place in this whole branch that calls `_do_discover_report`.
                discover_lines = build_discover_overlay(discover_result, error=discover_error)

                height, _width = stdscr.getmaxyx()
                banner = _console_banner()
                _paint(
                    stdscr,
                    _visible_slice([*banner, *discover_lines], discover_offset, height),
                )
                stdscr.timeout(int(interval * 1000))
                ch = stdscr.getch()
                if ch in (ord("q"), 27, ord("d")):
                    mode = "normal"
                    discover_offset = 0
                    # Discard the held result -- reopening the overlay is armed-but-not-run again.
                    discover_result = None
                    discover_error = None
                elif ch in (10, 13, curses.KEY_ENTER):
                    _paint(stdscr, [ScreenLine("contacting venue... please wait", "normal")])
                    try:
                        discover_result = _do_discover_report(open_state)
                        discover_error = None
                    except Exception as exc:
                        discover_result = None
                        # Truncated for the same reason `_refresh_balance`'s error is: a stray
                        # huge or sensitive blob (an HTTP error body, say) must never be painted
                        # full-screen verbatim.
                        discover_error = str(exc)[:200]
                    discover_offset = 0
                else:
                    # Banner-aware total, for the same reason as help's branch above.
                    discover_offset = _scroll_offset(
                        ch, discover_offset, height, len(banner) + len(discover_lines), curses
                    )
                continue

            # mode == "normal"
            now_ts = now_fn()
            try:
                # `Repository` exposes no public connection handle or `close()` (only the
                # private `_conn`), so there is nothing safe to close here each poll -- `repo`
                # simply falls out of scope and is garbage-collected.
                repo, config = open_state()
                report = gather_status(repo, config, now_ts)
                lines = build_screen(report, now_ts, available=available)
                if message is not None and now_ts - message_ts > _MESSAGE_TTL_SEC:
                    message = None
                if message is not None:
                    lines = [*lines, ScreenLine(message, _message_style(message))]
                _paint(stdscr, [*_console_banner(), *lines])
            except Exception as exc:
                # A transient read error (e.g. `sqlite3.OperationalError: database is locked`
                # from a concurrent `keel agent` writer) must never kill the dashboard --
                # paint an alert line and keep polling. `KeyboardInterrupt` is not caught here
                # (it isn't an `Exception`) so Ctrl-C still reaches the outer handler below.
                _paint(stdscr, [ScreenLine(f"status read failed: {exc} -- retrying...", "alert")])

            if now_ts - last_balance_ts >= _BALANCE_REFRESH_SEC:
                # A live broker call (`get_accounts`) on its own SLOW cadence -- not every
                # repaint, which would hammer the venue for no operator benefit. Deliberately
                # AFTER the paint above: the first iteration paints the dashboard immediately
                # (`available` is still `None`, so no balance line yet) rather than blocking the
                # first frame on a network call; the balance line appears on the NEXT repaint,
                # once this fetch completes. `_refresh_balance` is itself fail-soft, so a
                # broker/network failure here becomes a warn line next repaint, never a crash of
                # this loop.
                available = _refresh_balance(open_state, now_fn, _balance_fn)
                last_balance_ts = now_ts

            stdscr.timeout(int(interval * 1000))
            ch = stdscr.getch()
            if ch in (ord("q"), ord("Q")):
                break
            if ch in (ord("h"), ord("?")):
                mode = "help"
                help_offset = 0
                continue
            if ch == ord("i"):
                mode = "insights"
                insights_back = "normal"
                insights_offset = 0
                continue
            if ch == ord("s"):
                mode = "screen"
                screen_offset = 0
                continue
            if ch == ord("p"):
                mode = "propose"
                propose_offset = 0
                continue
            if ch == ord("v"):
                mode = "activity"
                activity_offset = 0
                activity_cursor = 0
                # Opens scoped to TODAY every single time, whatever the last visit widened it to.
                activity_scope = DEFAULT_ACTIVITY_SCOPE
                # Opens fully collapsed: the feed's value is the shape of the WHOLE run, and an
                # overlay that reopened with one cycle already exploded would bury it.
                activity_expanded = frozenset()
                continue
            if console_binding is not None and ch == ord("m"):
                # v6: the console menu over the whole dashboard -- bound only when a
                # console binding was supplied (the pre-C2 dashboard has no `m`).
                mode = "menu"
                menu_cursor = 0
                continue
            if ch == ord("d"):
                mode = "discover"
                discover_offset = 0
                # Always opens ARMED, not yet run -- even if a previous visit left a held result,
                # a fresh 'd' press starts over rather than silently showing stale data. (Closing
                # the overlay already clears these too; this is belt-and-braces.)
                discover_result = None
                discover_error = None
                continue
            if ch == ord("r"):
                last_balance_ts = 0  # force the balance to re-fetch on the next iteration too
                # Toast it. `r` always succeeds and usually changes nothing on screen (the DB
                # rarely differs between two repaints), so without this the key is
                # indistinguishable from a dead one -- see `_REFRESH_MESSAGE`.
                message = _REFRESH_MESSAGE
                message_ts = now_fn()
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
                # A fetch is a "refresh everything" gesture: re-read the live balance too, so a
                # deposit/sell that landed alongside the new candles shows up immediately.
                last_balance_ts = 0
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
    freshness, and subscriptions, auto-refreshing on an interval. Never places an order.

    Network touches are the exception, not the rule, and there are exactly three: an automatic
    read of the real account's spendable balance every ~30s (`get_accounts`, the same read rail
    13 funds a buy against -- see `run_live`'s docstring), `f` fetch (pulls candle history, no
    orders), and `d`+Enter inside the discover overlay (pulls the venue's product list, no
    orders). Everything else -- including the whole `s` screen / `p` propose / `d` discover
    admission workflow up until that one Enter keypress -- is DB/filesystem reads only.

    Press `h`/`?` for the in-app help (every keybinding and the safety notes); `i` opens a
    browsable, READ-ONLY insights overlay (per-rule track record, promotion-gate distance,
    account summary, and a recent-trades tail -- reusing `keel insights`' own pure
    builders/renderers verbatim); `s` opens a READ-ONLY screen overlay (the allowlist's current
    admission verdicts, reusing `keel assets screen`'s own gate); `p` opens a READ-ONLY propose
    overlay (screens the newest shortlist file in `config.proposals_dir`); `d` opens the discover
    overlay ARMED but not yet run -- it explains itself and waits for Enter before making its one
    live venue call, then holds that result until Enter is pressed again or the overlay closes;
    `v` opens the READ-ONLY activity feed -- a chronological, newest-first, one-row-per-cycle
    account of what the agent has actually been doing, read (offline, and boundedly) from the
    structured engine log rather than the DB, and expandable to the individual events inside any
    cycle. It is the answer to "keel has not traded -- is it even alive?", which the state-only
    dashboard cannot give: a quiet cycle still gets a row, and the run of them is the answer. It
    opens scoped to TODAY (the local calendar day) and `t` inside it widens to 7 days or to all
    the history the bounded read covers; a day with no cycle yet says when keel last ran and when
    the next run is due rather than showing an empty panel.
    None of `screen`/`propose`/`discover` attests, admits, or trades -- they only PROPOSE or
    REPORT. `attest` is invokable from the console's Compliance menu (and the scout browser's
    `a` step) but never on a keypress alone: the form ends in a typed confirmation -- type the
    asset code back; withdrawals attest types its own CLI phrase -- so the phrase, not
    CLI-only-ness, is the safety. `a`
    toggles autonomy (turning it OFF is instant, turning it ON needs a typed "yes" at the
    terminal, exactly like `keel autonomy on`); `f` fetches fresh candle history for every
    configured product (money-safe: no orders); `r` refreshes immediately. Quit with `q`.

    `m` opens the CONSOLE MENU (issue #388 C2): the menu tree over this dashboard -- the
    console shell. Profile switches the deployment (the config+db pair -- paper-forward,
    paper-hourly, paper-equities, live) in one action, rebinding everything the console
    reads; selecting LIVE asks an explicit y/N at the terminal first and is marked
    unmistakably in the header once active. Rules (issue #390 C4) opens the STRATEGY
    CONSOLE: the tried-vs-used ledger (every rule with its recorded lifecycle context --
    status, stamps, and the insights gate distance for paper rules; NO backtest runs on
    entry: each rule's verdict is an explicit, warned, per-rule re-compute on Enter),
    simulate + results (ARMED; Enter confirms a run that fetches and writes the report
    exactly as the CLI
    does, then holds the verdict and the report verbatim), add-a-strategy (per-field
    parameter help from the rule classes themselves, landing as candidate), retry
    (re-backtest + re-attempt promote with its y/N confirm and the TYPED `--force`
    phrase), enable/disable/demote, and insights. Research (C4) opens the evidence
    READERS: the experiments and research-docs corpora, the promotion reports (including
    a just-run simulation's, newest first), and the trials ledger with its chain verdict
    -- all read-only, all bounded reads. Compliance (issue #389 C3) opens the
    Compliance sub-menu: screen/propose/holdings/discover/subscription-show/purification
    as browsable service reports (holdings and discover ARMED -- one live venue read each,
    only on Enter), the record-writes (attest [typed: type the asset code back],
    attest-instrument, exempt/unexempt, subscription attest/set, withdrawals attest [typed
    'yes' when enabling -- the CLI's own gate, in-console]) as terminal forms that call the
    same services the CLI calls, the Scout results browser (the proposals directory from
    config, screened through the admission services, attest offered but never auto-run),
    and the read-only "Shariah in force" browser (the attestations, exemptions and
    fiqh-derived rails in force for the active profile, quoted and cited from
    docs/fiqh-basis.md, honesty lines always visible). Trading/Data/Account are
    placeholders for later console slices and say which one they land in. Every screen
    carries the session banner: the active deployment, then the venue's market session and
    clock (24/7, or OPEN/CLOSED with the next open/close, or CLOCK UNAVAILABLE fail-loud)
    -- rendered from the agent cycle's own recorded session state, never a clock call of
    the TUI's own. The shell itself adds no network touch.

    `--once` renders a single, static frame to stdout and exits without touching curses, for
    pipes/CI, matching `status`'s scripting-friendliness (and prints the disclaimer footer after
    the frame) -- none of the interactive actions (including `screen`/`propose`/`discover`) are
    available there, but the session banner still heads the frame so a piped snapshot names its
    deployment and market session. The default, interactive path owns the whole screen via
    `curses.wrapper` and re-opens the repo every poll so it reflects writes committed by a
    separate `keel agent` process.
    """
    if interval <= 0:
        raise click.ClickException("--interval must be > 0")

    # v6: the console binding IS the open_state -- the same `_load_cfg`/`_open_repo` pair
    # every CLI command uses, held mutable so the Profile menu can rebind it in one action.
    from keel.commands.console import ConsoleBinding, console_banner_lines

    console_binding = ConsoleBinding(
        ctx, config_path=ctx.obj["config_path"], db_path=ctx.obj["db_path"]
    )
    open_state: OpenState = console_binding.open_state

    now_fn: NowFn = lambda: int(time.time())  # noqa: E731

    if once:
        run_once(
            open_state,
            now_fn,
            click.echo,
            banner_fn=lambda repo, config, now_ts: console_banner_lines(
                console_binding, repo, config, now_ts
            ),
        )
        click.echo("")
        click.echo(DISCLAIMER)
        return

    if not _stdio_is_interactive():
        raise click.ClickException(
            "keel tui needs an interactive terminal (a real TTY on both stdin and stdout). "
            "Run it directly in a terminal, or use `keel tui --once` for a one-shot snapshot."
        )

    run_live(open_state, now_fn, interval, console_binding=console_binding)
