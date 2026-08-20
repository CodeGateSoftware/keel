"""HTML for the read surface. Every function here is PURE -- a report in, a string out.

The split matters more than it looks. `keel/commands/*` already returns frozen report dataclasses
and renders them to lines separately (`gather_status` / `render_human`, `build_insights_report` /
`render_summary`). This module is a THIRD renderer over the same reports, never a second place
that computes them -- which is what keeps `tests/commands/test_console_thinness.py` able to pin
the web layer with the rules it already applies to the console layer.

Rendering from the dataclasses rather than wrapping the terminal lines in `<pre>` is deliberate.
`<pre>` would have been a day's work instead of three, but it would freeze an 80-column terminal
layout into a medium that has no columns, and it would make the web UI a screenshot of the TUI
rather than a view of the data -- so every later improvement would have to start by undoing it.

No JavaScript, no external assets, no CDN. The page is one document with an inline stylesheet.
That is partly a packaging property -- D5 freezes this into a signed app bundle, and a build with
no asset pipeline is a build with nothing to go wrong -- and partly the same argument the rest of
the project makes: a page you can read the whole source of is a page you can audit.
"""

from __future__ import annotations

import html
import time
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

#: Nav order, and the labels. `/` first because the status page is the answer to "is it alive".
NAV: tuple[tuple[str, str], ...] = (
    ("/", "Status"),
    ("/setup", "Setup"),
    ("/activity", "Activity"),
    ("/insights", "Insights"),
    ("/rules", "Rules"),
    ("/venues", "Venues"),
    ("/gates", "Gates"),
    ("/glossary", "Glossary"),
)

_STYLE = """
:root {
  --bg: #fbfaf8; --fg: #1c1b19; --muted: #6b6862; --line: #e3dfd8;
  --card: #ffffff; --accent: #1f5f4f; --warn: #8a5a00; --bad: #96322a; --good: #1f5f4f;
}
:root:not([data-theme="light"]) { color-scheme: light dark; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #16150f; --fg: #ecead5; --muted: #9a968a; --line: #2f2d25;
    --card: #1d1c15; --accent: #6fbf9f; --warn: #d9a441; --bad: #e07a6a; --good: #6fbf9f;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { border-bottom: 1px solid var(--line); padding: 0.85rem 1.25rem; display: flex;
  flex-wrap: wrap; gap: 0.4rem 1.1rem; align-items: baseline; }
header .brand { font-weight: 650; letter-spacing: 0.02em; margin-right: 0.6rem; }
header a { color: var(--muted); text-decoration: none; padding: 0.15rem 0;
  border-bottom: 2px solid transparent; }
header a:hover { color: var(--fg); }
header a.on { color: var(--fg); border-bottom-color: var(--accent); }
main { max-width: 62rem; margin: 0 auto; padding: 1.5rem 1.25rem 4rem; }
h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
h2 { font-size: 1.05rem; margin: 2rem 0 0.6rem; }
.sub { color: var(--muted); margin: 0 0 1.5rem; font-size: 0.9rem; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 0.9rem 1.1rem; margin: 0 0 1rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.75rem; }
.kv { display: flex; flex-direction: column; gap: 0.15rem; }
.kv .k { color: var(--muted); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.05em; }
.kv .v { font-size: 1.05rem; font-variant-numeric: tabular-nums; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.42rem 0.7rem 0.42rem 0; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
th { color: var(--muted); font-weight: 550; font-size: 0.76rem; text-transform: uppercase;
  letter-spacing: 0.05em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; padding-right: 1.1rem; }
.pill { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 999px; font-size: 0.78rem;
  border: 1px solid var(--line); }
.good { color: var(--good); } .warn { color: var(--warn); } .bad { color: var(--bad); }
.muted { color: var(--muted); }
.empty { color: var(--muted); padding: 1.5rem 0; }
.note { color: var(--muted); font-size: 0.85rem; margin: 0.4rem 0 0; }
dl.terms dt { font-weight: 600; margin-top: 1rem; }
dl.terms dd { margin: 0.2rem 0 0; }
dl.terms dd.src { color: var(--muted); font-size: 0.82rem; }
footer { border-top: 1px solid var(--line); color: var(--muted); font-size: 0.8rem;
  padding: 1rem 1.25rem; }
pre { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 0.85rem; }
form { margin: 0.5rem 0 0; }
.field { display: flex; flex-direction: column; gap: 0.2rem; margin: 0.6rem 0; max-width: 26rem; }
.field span { font-size: 0.8rem; color: var(--muted); }
.field input { font: inherit; padding: 0.4rem 0.6rem; border-radius: 7px;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg); }
button { font: inherit; font-weight: 550; padding: 0.35rem 0.9rem; border-radius: 7px;
  border: 1px solid var(--accent); background: var(--accent); color: var(--card);
  cursor: pointer; }
button:hover { filter: brightness(1.08); }
"""


def esc(value: Any) -> str:
    """Everything reaching the page goes through here. Rule names, product ids and adapter error
    strings all originate outside this process; none of them is trusted markup."""
    return html.escape("" if value is None else str(value), quote=True)


def utc(ts: float | int | None, *, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """UTC, always, and labelled as such wherever it is shown.

    keel's day boundaries are UTC everywhere -- gates, scoping, the activity feed. Rendering in
    local time is what made the activity feed show a stale date (#381): the gate said one day and
    the rendering said another, and a "today" view could be permanently empty as a result."""
    if ts is None:
        return "--"
    try:
        return time.strftime(fmt, time.gmtime(float(ts)))
    except (OverflowError, OSError, ValueError):
        return "--"


def age(ts: float | int | None, now_ts: float | int | None) -> str:
    """How long ago, coarsely. A timestamp answers "when"; only this answers "is it stale"."""
    if ts is None or now_ts is None:
        return ""
    delta = int(float(now_ts) - float(ts))
    if delta < 0:
        return "in the future"
    if delta < 90:
        return f"{delta}s ago"
    if delta < 5400:
        return f"{delta // 60}m ago"
    if delta < 172800:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def money(value: Decimal | None, *, places: int = 2) -> str:
    """Display only -- never arithmetic. Rule 3 of the thinness pin forbids operating on a
    `Decimal` in this layer, and the reports hand over every figure already computed."""
    if value is None:
        return "--"
    return f"{value:,.{places}f}"


def pct(value: Decimal | float | None, *, places: int = 2) -> str:
    if value is None:
        return "--"
    return f"{value:.{places}f}%"


def kv(key: str, value: str, *, tone: str = "") -> str:
    cls = f' class="v {tone}"' if tone else ' class="v"'
    return f'<div class="kv"><span class="k">{esc(key)}</span><span{cls}>{value}</span></div>'


def table(headers: Sequence[tuple[str, bool]], rows: Iterable[Sequence[str]]) -> str:
    """`headers` is `(label, numeric)`; cell values are ALREADY escaped by the caller, because
    several columns are deliberately markup (a tone span, a pill)."""
    head = "".join(
        f'<th class="num">{esc(label)}</th>' if numeric else f"<th>{esc(label)}</th>"
        for label, numeric in headers
    )
    body_rows = []
    for row in rows:
        cells = "".join(
            f'<td class="num">{cell}</td>' if numeric else f"<td>{cell}</td>"
            for cell, (_, numeric) in zip(row, headers, strict=False)
        )
        body_rows.append(f"<tr>{cells}</tr>")
    if not body_rows:
        return ""
    return (
        '<div class="tablewrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


def page(
    *,
    title: str,
    path: str,
    body: str,
    build: str = "",
    refresh_sec: int | None = None,
) -> str:
    """The document shell. `refresh_sec` emits a `<meta http-equiv="refresh">` -- a zero-JS
    auto-update, which keeps the "no scripts at all" property that makes this page auditable and
    trivially freezable. The cost is a full reload rather than a patch; on a page that is a few
    kilobytes of local HTML, that is not a cost."""
    nav_items = []
    for href, label in NAV:
        on = ' class="on"' if href == path else ""
        nav_items.append(f'<a href="{esc(href)}"{on}>{esc(label)}</a>')
    nav = "".join(nav_items)
    meta_refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_sec)}">' if refresh_sec else ""
    )
    refresh_note = (
        f"this page reloads every {int(refresh_sec)}s" if refresh_sec else "read-only view"
    )
    return (
        f"<title>{esc(title)} - keel</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"{meta_refresh}"
        f"<style>{_STYLE}</style>"
        f'<header><span class="brand">keel</span>{nav}</header>'
        f"<main>{body}</main>"
        f"<footer>{esc(refresh_note)}"
        + (f" &middot; {esc(build)}" if build else "")
        + " &middot; keel does not give financial advice</footer>"
    )


def _tone_for_rail(status: str) -> str:
    lowered = (status or "").lower()
    if "breach" in lowered or "halt" in lowered or "trip" in lowered:
        return "bad"
    if "warn" in lowered or "near" in lowered:
        return "warn"
    return "good"


def render_status(report: Any) -> str:
    """The `keel status` report, as the landing page -- the same `StatusReport` the CLI renders
    to lines and `--json` serialises, never a re-gather."""
    autonomy = report.autonomy
    autonomy_text = "on" if getattr(autonomy, "enabled", False) else "off"
    parts = [
        f'<h1>Status</h1><p class="sub">{esc(utc(report.now_ts))} UTC</p>',
        '<div class="card"><div class="grid">',
        kv("mode", esc(report.mode)),
        kv(
            "kill switch",
            "ENGAGED" if report.kill_switch_engaged else "clear",
            tone="bad" if report.kill_switch_engaged else "good",
        ),
        kv("autonomy", esc(autonomy_text), tone="warn" if autonomy_text == "on" else "muted"),
        kv("rail 11", esc(report.rail11_status), tone=_tone_for_rail(report.rail11_status)),
        "</div></div>",
        '<div class="card"><div class="grid">',
        kv("high water mark", esc(money(report.high_water_mark))),
        kv("drawdown (total)", esc(pct(report.drawdown_total_pct))),
        kv("drawdown (weekly)", esc(pct(report.drawdown_weekly_pct))),
        kv("max total dd", esc(pct(report.max_total_dd_pct))),
        kv("max weekly dd", esc(pct(report.max_weekly_dd_pct))),
        kv("paper cash", esc(money(report.paper_cash_usdc))),
        "</div></div>",
    ]

    attestation = report.withdrawal_attestation
    expired = bool(getattr(attestation, "expired", False))
    parts.append(
        '<div class="card"><div class="grid">'
        + kv(
            "withdrawal attestation (rail 17)",
            "EXPIRED" if expired else "fresh",
            tone="bad" if expired else "good",
        )
        + "</div></div>"
    )

    parts.append("<h2>Open positions</h2>")
    position_rows = [
        (
            esc(getattr(pos, "product_id", "")),
            esc(getattr(pos, "rule_name", "") or "--"),
            esc(money(getattr(pos, "qty", None), places=8)),
            esc(money(getattr(pos, "entry_fill", None))),
            esc(utc(getattr(pos, "opened_at", None), fmt="%Y-%m-%d %H:%M")),
        )
        for pos in report.open_positions
    ]
    parts.append(
        table(
            (
                ("product", False),
                ("rule", False),
                ("qty", True),
                ("entry", True),
                ("opened (UTC)", False),
            ),
            position_rows,
        )
        or '<p class="empty">No open positions.</p>'
    )

    parts.append("<h2>Rules</h2>")
    counts = " &middot; ".join(
        f'{esc(name)} <span class="muted">{esc(count)}</span>'
        for name, count in sorted(report.rule_counts.items())
    )
    parts.append(f'<p class="note">{counts or "no rules"}</p>')
    live_rows = [
        (
            esc(getattr(rule, "name", "")),
            esc(getattr(rule, "status", "")),
            esc(getattr(rule, "kind", "")),
        )
        for rule in report.live_rules
    ]
    parts.append(
        table((("live rule", False), ("status", False), ("kind", False)), live_rows)
        or '<p class="empty">No live rules.</p>'
    )

    parts.append("<h2>Data freshness</h2>")
    fresh_rows = [
        (
            esc(getattr(row, "product_id", "")),
            esc(getattr(row, "granularity", "") or "--"),
            esc(utc(getattr(row, "last_ts", None), fmt="%Y-%m-%d %H:%M")),
            esc(age(getattr(row, "last_ts", None), report.now_ts)),
        )
        for row in report.data_freshness
    ]
    parts.append(
        table(
            (
                ("product", False),
                ("granularity", False),
                ("last candle (UTC)", False),
                ("age", False),
            ),
            fresh_rows,
        )
        or '<p class="empty">No market data yet.</p>'
    )

    parts.append("<h2>Subscriptions</h2>")
    sub_rows = [
        (
            esc(getattr(row, "venue", "") or getattr(row, "name", "")),
            esc(getattr(row, "status", "")),
            esc(utc(getattr(row, "attested_ts", None), fmt="%Y-%m-%d")),
        )
        for row in report.subscriptions
    ]
    parts.append(
        table((("venue", False), ("status", False), ("attested (UTC)", False)), sub_rows)
        or '<p class="empty">No subscription attestations.</p>'
    )
    return "".join(parts)


def render_activity(feed: Any) -> str:
    """The activity feed. Everything shown here is `ActivityFeed`'s own vocabulary -- including
    the non-`ok` statuses, which are rendered as prose rather than suppressed: `missing` is the
    commonest state on a fresh install and is not an error, and hiding it would leave a user
    staring at a blank panel with nothing to act on."""
    explain = {
        "missing": "No log file yet. This is normal before the first cycle -- it also happens when "
        "keel is run from a directory that is not the deployment folder.",
        "empty": "The log exists but the window held no records.",
        "unparseable": "Lines were read, but none of them was a JSON record.",
        "oversized": "The bounded tail read landed inside a single record, so nothing whole "
        "survived it.",
        "unreadable": "The log could not be read.",
    }
    parts = [
        '<h1>Activity</h1><p class="sub">'
        + esc(feed.source or "no source")
        + " &middot; "
        + esc(feed.scope)
        + " &middot; UTC</p>"
    ]
    if feed.status != "ok":
        detail = f" {esc(feed.detail)}" if feed.detail else ""
        parts.append(
            f'<div class="card"><strong>{esc(feed.status)}</strong> '
            f'<span class="muted">{esc(explain.get(feed.status, ""))}{detail}</span></div>'
        )

    rows = []
    for cycle in feed.cycles:
        tone = "muted" if cycle.is_quiet else ""
        label = cycle.cycle_id or "uncorrelated"
        rows.append(
            (
                f'<span class="{tone}">{esc(utc(cycle.started_ts, fmt="%m-%d %H:%M:%S"))}</span>',
                esc(label[:12]),
                esc(cycle.mode or "--"),
                esc(", ".join(cycle.products) or "--"),
                esc(cycle.signals),
                esc(cycle.blocked),
                esc(cycle.entered),
                esc(cycle.exited),
                f'<span class="bad">{esc(cycle.errors)}</span>' if cycle.errors else "0",
                esc("; ".join(cycle.highlights)),
            )
        )
    parts.append(
        table(
            (
                ("started (UTC)", False),
                ("cycle", False),
                ("mode", False),
                ("products", False),
                ("signals", True),
                ("blocked", True),
                ("entered", True),
                ("exited", True),
                ("errors", True),
                ("highlights", False),
            ),
            rows,
        )
        or '<p class="empty">Nothing in this scope.</p>'
    )

    notes = []
    if feed.cycles_out_of_scope:
        notes.append(f"{feed.cycles_out_of_scope} cycle(s) hidden by the scope")
    if not feed.scope_fully_covered:
        notes.append(
            "the bounded read did not prove it reached the scope boundary, so an empty view here "
            "does not mean the scope was quiet"
        )
    if feed.lines_skipped:
        notes.append(f"{feed.lines_skipped} unusable line(s) skipped")
    if feed.window_truncated:
        notes.append("the log window was truncated")
    if feed.cycles_dropped:
        notes.append(f"{feed.cycles_dropped} cycle(s) beyond the display cap")
    if feed.last_cycle_before_scope is not None:
        notes.append(
            "last cycle before this scope: "
            + utc(feed.last_cycle_before_scope.started_ts, fmt="%Y-%m-%d %H:%M")
        )
    if notes:
        parts.append('<p class="note">' + " &middot; ".join(esc(note) for note in notes) + "</p>")
    return "".join(parts)


def render_insights(report: Any, journal: Any) -> str:
    account = report.account
    parts = [
        f'<h1>Insights</h1><p class="sub">{esc(utc(report.now_ts))} UTC &middot; '
        f"{esc(report.closed_trade_count)} closed trade(s)</p>",
        '<div class="card"><div class="grid">',
        kv("mode", esc(account.mode)),
        kv("rail 11", esc(account.rail11_status), tone=_tone_for_rail(account.rail11_status)),
        kv("high water mark", esc(money(account.high_water_mark))),
        kv("drawdown (total)", esc(pct(account.drawdown_total_pct))),
        kv("drawdown (weekly)", esc(pct(account.drawdown_weekly_pct))),
        "</div></div>",
        "<h2>Rule track records</h2>",
    ]
    rows = []
    for rule in report.rules:
        significance = (
            '<span class="pill">n&ge;30</span>'
            if rule.significant
            else '<span class="pill warn">below the n=30 floor</span>'
        )
        rows.append(
            (
                esc(rule.rule_name),
                esc(rule.status),
                esc(rule.n_trades),
                esc(f"{rule.win_rate:.1f}%"),
                esc(money(rule.expectancy, places=4)),
                esc(money(rule.profit_factor, places=2)),
                esc(money(rule.max_drawdown)),
                significance,
            )
        )
    parts.append(
        table(
            (
                ("rule", False),
                ("status", False),
                ("trades", True),
                ("win rate", True),
                ("expectancy", True),
                ("profit factor", True),
                ("max dd", True),
                ("sample", False),
            ),
            rows,
        )
        or '<p class="empty">No rules with a track record yet.</p>'
    )
    parts.append(
        '<p class="note">Below 30 closed trades a win rate is not yet distinguishable from '
        "random entry, which is why the sample column says so rather than leaving the number to "
        "speak for itself.</p>"
    )

    parts.append(
        f'<h2>Journal</h2><p class="note">{esc(journal.total_count)} closed trade(s) total.</p>'
    )
    journal_rows = []
    for entry in journal.entries:
        if entry.pnl_net is None:
            # "--", never "0.00": a trade with no recorded net is not a break-even trade.
            pnl_cell = "--"
        else:
            tone = "good" if entry.pnl_net >= 0 else "bad"
            pnl_cell = f'<span class="{tone}">{esc(money(entry.pnl_net))}</span>'
        journal_rows.append(
            (
                esc(utc(entry.closed_at, fmt="%Y-%m-%d %H:%M")),
                esc(entry.product_id),
                esc(entry.rule_name or "--"),
                esc(money(entry.qty, places=8)),
                esc(money(entry.entry_fill)),
                esc(money(entry.exit_fill)),
                pnl_cell,
                esc(money(entry.fees, places=4)),
                esc(entry.outcome),
            )
        )
    parts.append(
        table(
            (
                ("closed (UTC)", False),
                ("product", False),
                ("rule", False),
                ("qty", True),
                ("entry", True),
                ("exit", True),
                ("net p&l", True),
                ("fees", True),
                ("outcome", False),
            ),
            journal_rows,
        )
        or '<p class="empty">No closed trades.</p>'
    )
    return "".join(parts)


def render_rules(rows: Sequence[dict[str, Any]]) -> str:
    parts = ['<h1>Rules</h1><p class="sub">read-only &middot; promotion happens in the CLI</p>']
    table_rows = [
        (
            esc(row.get("id")),
            esc(row.get("kind")),
            esc(row.get("status")),
            f"<pre>{esc(row.get('params'))}</pre>",
        )
        for row in rows
    ]
    parts.append(
        table((("id", True), ("kind", False), ("status", False), ("params", False)), table_rows)
        or '<p class="empty">No rules.</p>'
    )
    return "".join(parts)


def render_venues(infos: Sequence[Any]) -> str:
    """Capability rows, exactly as `keel brokers list` declares them -- what the ADAPTER says it
    can do, never an inference about the operator's keys. A row here is not a claim that the venue
    is configured or reachable (#233)."""
    parts = [
        '<h1>Venues</h1><p class="sub">what each installed adapter declares &mdash; not whether '
        "it is configured</p>"
    ]
    rows = []
    for info in infos:
        if info.error:
            rows.append(
                (
                    esc(info.name),
                    '<span class="bad">adapter failed to construct</span>',
                    f'<span class="bad">{esc(info.error)}</span>',
                    "",
                    "",
                    "",
                )
            )
            continue
        rows.append(
            (
                esc(info.name),
                esc(info.venue),
                esc(info.deployment),
                esc(", ".join(info.asset_classes) or "--"),
                esc(", ".join(info.supported_orders) or "--"),
                esc(info.package_version or "--"),
            )
        )
    parts.append(
        table(
            (
                ("adapter", False),
                ("venue", False),
                ("deployment", False),
                ("asset classes", False),
                ("orders", False),
                ("version", False),
            ),
            rows,
        )
        or '<p class="empty">No adapters installed.</p>'
    )
    return "".join(parts)


#: How each kind of setup step is introduced. The wording carries the whole argument of #437 --
#: what a wizard may do for you, what it may only collect, and what it cannot touch at all.
_STEP_KIND_NOTE: dict[str, str] = {
    "mechanical": "keel can do this for you.",
    "judgement": (
        "Yours to decide. keel can record it; it must never choose it for you, and an "
        "attestation without a cited source is refused exactly like a missing one."
    ),
    "off_venue": (
        "Happens in the venue's own dashboard, and keel cannot verify it -- the venue's API "
        "does not expose it. Never shown as done here, because a green check that verifies "
        "nothing turns an open risk into a false assurance."
    ),
}


def render_setup(
    state: Any,
    *,
    actions: Sequence[Any] = (),
    not_automated: dict[str, str] | None = None,
    csrf: str = "",
    ran: str = "",
) -> str:
    """The first-run checklist, with a button for each MECHANICAL step and a command for every
    other one.

    A button appears ONLY where `actions` carries one, and `actions` is
    `keel.commands.setup.ACTIONS` -- a closed set that cannot contain a judgement or off-venue
    step without failing a test. So this function cannot grow a button for "attest this asset" by
    someone adding markup here: it would have to be added to the registry first, where the test
    is."""
    by_key = {action.key: action for action in actions}
    not_automated = not_automated or {}
    parts = [
        "<h1>Setup</h1>",
        f'<p class="sub">{esc(state.root)}</p>',
    ]
    if ran:
        item = next((s for s in state.states if s.step.key == ran), None)
        if item is not None:
            done = item.done is True
            parts.append(
                f'<div class="card"><strong class="{"good" if done else "warn"}">'
                f"{esc(item.step.title)}</strong> "
                f'<span class="muted">{esc(item.detail)}</span></div>'
            )
    if state.is_new:
        parts.append(
            '<div class="card"><strong>There is no deployment here yet.</strong> '
            '<span class="muted">Nothing below has been done, which is exactly what a first '
            "run looks like. Work down the list; the paper stage places no orders at all.</span>"
            "</div>"
        )
    nxt = state.next_step
    if nxt is not None:
        parts.append(
            '<div class="card"><div class="kv"><span class="k">next</span>'
            f'<span class="v">{esc(nxt.step.title)}</span></div>'
            f'<p class="note">{esc(nxt.step.how)}</p></div>'
        )
    else:
        parts.append('<div class="card">Nothing outstanding.</div>')

    for stage, heading, blurb in (
        (
            "paper",
            "To run in paper",
            "Evaluates rules against real market data and places nothing.",
        ),
        ("live", "To go live", "Everything the go-live runbook adds before real money moves."),
    ):
        items = [item for item in state.states if item.step.stage.value == stage]
        parts.append(f"<h2>{esc(heading)}</h2>")
        parts.append(f'<p class="note">{esc(blurb)}</p>')
        rows = []
        for item in items:
            if item.done is True:
                mark = '<span class="good">done</span>'
            elif item.done is False:
                mark = '<span class="muted">to do</span>'
            else:
                mark = '<span class="warn">not determined</span>'
            note = _STEP_KIND_NOTE.get(item.step.kind.value, "")
            body = (
                f"<strong>{esc(item.step.title)}</strong>"
                f'<div class="muted">{esc(item.detail)}</div>'
            )
            action = by_key.get(item.step.key)
            if item.blocking and action is not None and csrf:
                body += _action_form(action, csrf)
            elif item.blocking:
                body += f"<div><code>{esc(item.step.how)}</code></div>"
                if item.step.key in not_automated:
                    body += (
                        '<div class="muted">Not a button, deliberately: '
                        f"{esc(not_automated[item.step.key])}</div>"
                    )
            body += f'<div class="muted">{esc(item.step.why)} {esc(note)}</div>'
            rows.append((mark, f'<span class="pill">{esc(item.step.kind.value)}</span>', body))
        parts.append(table((("", False), ("kind", False), ("step", False)), rows))
    return "".join(parts)


def _action_form(action: Any, csrf: str) -> str:
    """One action key, one write token, and only the fields the action itself declares.

    A field marked `secret` renders as `type="password"` and is NEVER given a `value` -- not even
    on a re-render after a failure. Pre-filling a secret field puts the secret in the page source,
    where it survives a screenshot, a "view source", and anything that saves the page. The cost is
    that a failed submission must be retyped; that is the correct cost.

    `autocomplete="off"` on the secret fields keeps a browser password manager from offering to
    store an exchange API key as though it were a website login."""
    fields = ""
    for field in getattr(action, "inputs", ()):
        kind = "password" if field.secret else "text"
        extra = ' autocomplete="off" spellcheck="false"' if field.secret else ""
        fields += (
            '<label class="field">'
            f"<span>{esc(field.label)}</span>"
            f'<input type="{kind}" name="{esc(field.name)}" required{extra}>'
            "</label>"
        )
    return (
        f'<form method="post" action="/setup/{esc(action.key)}">'
        f'<input type="hidden" name="csrf" value="{esc(csrf)}">'
        f'<div class="muted">{esc(action.detail)}</div>'
        f"{fields}"
        f'<button type="submit">{esc(action.title)}</button>'
        "</form>"
    )


def render_gates(gates: Sequence[Any], capabilities: Sequence[Any]) -> str:
    """The capability inventory (#436), rendered from `keel/capabilities.py`.

    This page is the reason a browser view can be honest about its own limits. The read surface
    here cannot reach a single one of these actions -- the server implements no write verb at all
    -- and the page says so, next to the list of what it cannot do and who can."""
    parts = [
        '<h1>Gates</h1><p class="sub">every action that increases what keel can do without '
        "asking again</p>",
        '<div class="card"><strong>This view cannot perform any of them.</strong> '
        '<span class="muted">The server answers GET and HEAD and implements no other verb, so '
        "there is no request it can accept that changes anything. Each action below needs a "
        "human at a terminal.</span></div>",
    ]
    for gate in gates:
        covered = [cap for cap in capabilities if cap.gate == gate.name]
        parts.append(f"<h2>{esc(gate.name)} &middot; {esc(len(covered))} action(s)</h2>")
        parts.append(
            '<div class="card"><div class="kv"><span class="k">evidence required</span>'
            f'<span class="v">{esc(gate.evidence)}</span></div>'
            f'<p class="note">Fails closed against {esc(gate.fails_closed_against)}.</p>'
            f'<p class="note">Implemented once, at <code>{esc(gate.implementation)}</code>.</p>'
            "</div>"
        )
        rows = []
        for cap in covered:
            mirror = (
                f'<span class="pill">mirrors {esc(cap.mirrors[1])}</span>' if cap.mirrors else ""
            )
            rows.append(
                (
                    f'<span class="pill">{esc(cap.surface)}</span>',
                    f"<code>{esc(cap.invocation)}</code> {mirror}",
                    esc(cap.increases),
                    f"<code>{esc(cap.module)}.{esc(cap.function)}</code>",
                )
            )
        parts.append(
            table(
                (("surface", False), ("action", False), ("grants", False), ("call site", False)),
                rows,
            )
        )
    return "".join(parts)


def render_glossary(terms: Sequence[Any]) -> str:
    parts = [
        '<h1>Glossary</h1><p class="sub">keel\'s vocabulary, and the fiqh terms it anchors to</p>',
        '<dl class="terms">',
    ]
    for term in terms:
        marker = ' <span class="pill">fiqh</span>' if term.fiqh else ""
        if term.fiqh and not term.stated:
            marker = ' <span class="pill warn">not stated in fiqh-basis</span>'
        parts.append(f"<dt>{esc(term.term)}{marker}</dt>")
        parts.append(f"<dd>{esc(term.definition)}</dd>")
        source = term.citation or term.source
        if source:
            parts.append(f'<dd class="src">{esc(source)}</dd>')
    parts.append("</dl>")
    if not terms:
        # The normal state of an INSTALLED deployment, not a bug: `docs/glossary.md` is read from
        # the working directory, and a deployment folder is a config, a database and an .env --
        # there is no docs checkout beside them. The TUI's help screen shows the same empty state
        # for the same reason. Packaging the docs inside the artifact is D5's business (#438);
        # until then this says which file is missing rather than implying the glossary is empty.
        parts.append(
            '<p class="empty">No glossary here. keel reads <code>docs/glossary.md</code> from '
            "the folder it is run in, and an installed deployment has no docs checkout beside "
            "its config and database.</p>"
        )
    return "".join(parts)


def render_message(heading: str, detail: str) -> str:
    return f'<h1>{esc(heading)}</h1><p class="sub">{esc(detail)}</p>'
