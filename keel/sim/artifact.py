"""HTML/SVG simulation artifact (Sim Task 9,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md` Task 9).

`render_html` produces one **fully self-contained** HTML document -- no external requests, no CDN
links, no `<script>`, all CSS inlined in a `<style>` block, and every chart hand-emitted as inline
`<svg>` (Global Constraints: stdlib-only, no new deps, no NumPy/Pandas/Matplotlib). It is meant to
sit next to the Markdown report `keel.sim.report.render_markdown` produces (same content, same
verdict/gaps, plus visuals the Markdown table can't carry) and is written to disk by the CLI's
`--artifact` flag (`keel/cli.py`), never served or fetched over the network.

**The `Decimal -> float` boundary**: every money/price/return value in this module is a `Decimal`
until the moment it becomes an SVG pixel coordinate inside `_svg_line`/`_svg_drawdown`/`_svg_bars`
-- those three functions are the *only* place `float()` is ever called on a `Decimal`. Every value
rendered as *text* (the verdict box, the metrics table, the gap table, the per-asset P&L labels)
is a `Decimal` or a string formatted straight from one (`_fmt_money`/`_fmt_pct`), never a
stringified float. `ts` (an epoch-seconds `int`) is used directly as a float-safe axis coordinate
input -- ints are exact in float up to 2**53, far beyond any epoch timestamp this project handles.

Pure functions only: no clock reads (`time.time()`/`datetime.now()`), no file I/O, no network --
matching `keel/sim/report.py`'s contract exactly.
"""

from __future__ import annotations

import html as _html
from decimal import Decimal

from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimResult
from keel.sim.report import GapItem, Verdict

__all__ = ["render_html"]

# Chart geometry (pixels, SVG viewBox units -- purely a layout constant, not data).
_CHART_WIDTH = 780
_CHART_HEIGHT = 260
_CHART_PAD = 44
_BARS_HEIGHT = 220

_LINE_COLORS = ("#4C6EF5", "#F76707", "#12B886", "#E64980")
_POSITIVE_BAR = "#12B886"
_NEGATIVE_BAR = "#E03131"

# (account_metrics key, display label) -- mirrors `report._ACCOUNT_METRIC_LABELS`'s selection so
# the HTML artifact's metrics table reads the same as the Markdown report's "Account results"
# section; kept as its own tuple here rather than importing the private list so this module has
# no dependency on `report.py`'s internals, only its public dataclasses.
_METRIC_LABELS: tuple[tuple[str, str], ...] = (
    ("contributed", "Contributed"),
    ("ending_value", "Ending value"),
    ("net_pnl_usd", "Net P&L ($)"),
    ("total_return_pct", "Total return"),
    ("irr", "IRR"),
    ("cagr", "CAGR"),
    ("max_drawdown_pct", "Max drawdown"),
    ("return_per_drawdown", "Return / drawdown"),
    ("sharpe", "Sharpe"),
    ("sortino", "Sortino"),
    ("time_in_market_pct", "Time in market"),
    ("trade_count", "Trade count"),
    ("avg_hold_hours", "Avg hold (hrs)"),
    ("allowance_utilization_pct", "Allowance utilization"),
)


# ---------------------------------------------------------------------------
# Small text/formatting helpers -- Decimal in, string out, no float anywhere.
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return _html.escape(str(text), quote=True)


def _fmt_value(value: object) -> str:
    """Render an account-metric/table cell value as text. `Decimal`s keep full precision (no
    float round-trip); everything else is just `str()`."""
    if isinstance(value, Decimal):
        return f"{value:,}"
    return str(value)


# ---------------------------------------------------------------------------
# SVG charts -- Decimal -> float ONLY at the pixel-coordinate boundary below.
# ---------------------------------------------------------------------------


def _empty_svg(width: int, height: int, message: str) -> str:
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'class="chart" role="img" aria-label="{_esc(message)}">'
        f'<text x="{width / 2:.2f}" y="{height / 2:.2f}" class="empty-msg" '
        f'text-anchor="middle">{_esc(message)}</text></svg>'
    )


def _svg_line(
    series_list: list[tuple[str, list[tuple[int, Decimal]], str]],
    width: int = _CHART_WIDTH,
    height: int = _CHART_HEIGHT,
    pad: int = _CHART_PAD,
    title: str = "",
) -> str:
    """Hand-emitted inline `<svg>` line chart -- one `<polyline>` per `(label, series, color)`
    entry in `series_list`, plus axes and a legend. Used for the engine-vs-benchmark equity
    curve, but generic over any number of series.

    `ts` (already an `int`) and each `Decimal` value are converted to `float` here -- and only
    here -- purely to compute pixel positions; nothing in the returned markup is a rendered money
    value (labels are the caller-supplied series names, not amounts).
    """
    points = [(ts, value) for _, series, _ in series_list for ts, value in series]
    if not points:
        return _empty_svg(width, height, title or "no data")

    xs = [float(ts) for ts, _ in points]
    ys = [float(value) for _, value in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    if max_x == min_x:
        max_x = min_x + 1.0
    if max_y == min_y:
        max_y = min_y + 1.0

    plot_w = width - 2 * pad
    plot_h = height - 2 * pad

    def x_px(ts: int) -> float:
        return pad + (float(ts) - min_x) / (max_x - min_x) * plot_w

    def y_px(value: Decimal) -> float:
        return pad + plot_h - (float(value) - min_y) / (max_y - min_y) * plot_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" class="chart" '
        f'role="img" aria-label="{_esc(title)}">'
    ]
    if title:
        parts.append(
            f'<text x="{pad}" y="{pad / 2:.2f}" class="chart-title">{_esc(title)}</text>'
        )
    # axes
    parts.append(
        f'<line x1="{pad}" y1="{height - pad:.2f}" x2="{width - pad}" y2="{height - pad:.2f}" '
        'class="axis" />'
    )
    parts.append(f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad:.2f}" class="axis" />')

    legend_y = pad + 14
    for i, (label, series, color) in enumerate(series_list):
        if not series:
            continue
        line_color = color or _LINE_COLORS[i % len(_LINE_COLORS)]
        pts = " ".join(f"{x_px(ts):.2f},{y_px(value):.2f}" for ts, value in series)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{line_color}" stroke-width="2" />'
        )
        parts.append(f'<circle cx="{pad + 10}" cy="{legend_y:.2f}" r="4" fill="{line_color}" />')
        parts.append(
            f'<text x="{pad + 20}" y="{legend_y + 4:.2f}" class="legend">{_esc(label)}</text>'
        )
        legend_y += 16

    parts.append("</svg>")
    return "\n".join(parts)


def _svg_drawdown(
    equity_curve: list[tuple[int, Decimal]],
    width: int = _CHART_WIDTH,
    height: int = _CHART_HEIGHT,
    pad: int = _CHART_PAD,
) -> str:
    """Underwater/drawdown area plot: `(peak - value) / peak` (a `Decimal`, always `<= 0` here,
    plotted as a negative percentage) at every `equity_curve` point, filled from the zero
    baseline down to the curve.
    """
    if not equity_curve:
        return _empty_svg(width, height, "no drawdown data")

    peak: Decimal | None = None
    dd_series: list[tuple[int, Decimal]] = []
    for ts, value in equity_curve:
        if peak is None or value > peak:
            peak = value
        dd = -((peak - value) / peak) if peak and peak != 0 else Decimal(0)
        dd_series.append((ts, dd))

    xs = [float(ts) for ts, _ in dd_series]
    ys = [float(dd) for _, dd in dd_series]
    min_x, max_x = min(xs), max(xs)
    min_y = min(ys + [0.0])
    max_y = 0.0
    if max_x == min_x:
        max_x = min_x + 1.0
    if min_y == max_y:
        min_y = -1.0

    plot_w = width - 2 * pad
    plot_h = height - 2 * pad

    def x_px(ts: int) -> float:
        return pad + (float(ts) - min_x) / (max_x - min_x) * plot_w

    def y_px(value: float) -> float:
        return pad + plot_h - (value - min_y) / (max_y - min_y) * plot_h

    baseline_y = y_px(0.0)
    curve_pts = [(x_px(ts), y_px(float(dd))) for ts, dd in dd_series]
    polygon_pts = (
        f"{curve_pts[0][0]:.2f},{baseline_y:.2f} "
        + " ".join(f"{x:.2f},{y:.2f}" for x, y in curve_pts)
        + f" {curve_pts[-1][0]:.2f},{baseline_y:.2f}"
    )
    outline_pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in curve_pts)

    max_dd_pct = min(dd_series, key=lambda p: p[1])[1] if dd_series else Decimal(0)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" class="chart" '
        'role="img" aria-label="underwater drawdown chart">',
        f'<text x="{pad}" y="{pad / 2:.2f}" class="chart-title">'
        f"Drawdown (max {max_dd_pct:.2%})</text>",
        f'<line x1="{pad}" y1="{baseline_y:.2f}" x2="{width - pad}" y2="{baseline_y:.2f}" '
        'class="axis" />',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad:.2f}" class="axis" />',
        f'<polygon points="{polygon_pts}" class="drawdown-area" />',
        f'<polyline points="{outline_pts}" fill="none" class="drawdown-line" stroke-width="2" />',
        "</svg>",
    ]
    return "\n".join(parts)


def _svg_bars(
    per_asset_pnl: dict[str, Decimal],
    width: int = _CHART_WIDTH,
    height: int = _BARS_HEIGHT,
    pad: int = _CHART_PAD,
) -> str:
    """Per-asset realized P&L bar chart, one `<rect>` per asset (sorted for determinism), colored
    by sign. Each bar's dollar value is also rendered as `<text>` -- formatted straight from the
    `Decimal` (`_fmt_value`), not from the `float` used to compute the bar's pixel height.
    """
    if not per_asset_pnl:
        return _empty_svg(width, height, "no per-asset P&L data")

    items = sorted(per_asset_pnl.items())
    values = [float(pnl) for _, pnl in items]
    max_v = max(values + [0.0])
    min_v = min(values + [0.0])
    if max_v == min_v:
        max_v = min_v + 1.0

    plot_w = width - 2 * pad
    plot_h = height - 2 * pad - 16  # leave room for asset-name labels under the axis

    def y_px(value: float) -> float:
        return pad + plot_h - (value - min_v) / (max_v - min_v) * plot_h

    zero_y = y_px(0.0)
    bar_w = plot_w / len(items)
    margin = bar_w * 0.15

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" class="chart" '
        'role="img" aria-label="per-asset P&amp;L bar chart">',
        f'<line x1="{pad}" y1="{zero_y:.2f}" x2="{width - pad}" y2="{zero_y:.2f}" class="axis" />',
    ]
    for i, (asset, pnl) in enumerate(items):
        value = float(pnl)
        x = pad + i * bar_w
        top = y_px(max(value, 0.0))
        bottom = y_px(min(value, 0.0))
        bar_height = max(bottom - top, 1.0)
        color = _POSITIVE_BAR if value >= 0 else _NEGATIVE_BAR
        parts.append(
            f'<rect x="{x + margin:.2f}" y="{top:.2f}" width="{bar_w - 2 * margin:.2f}" '
            f'height="{bar_height:.2f}" fill="{color}" />'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.2f}" y="{height - 4}" class="bar-label" '
            f'text-anchor="middle">{_esc(asset)}</text>'
        )
        value_y = top - 4 if value >= 0 else bottom + 14
        parts.append(
            f'<text x="{x + bar_w / 2:.2f}" y="{value_y:.2f}" class="bar-value" '
            f'text-anchor="middle">{_esc(_fmt_value(pnl))}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# HTML sections
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  margin: 0; padding: 1.5rem; line-height: 1.5;
  background: #ffffff; color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
  body { background: #14161a; color: #e8e8e8; }
}
h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid rgba(128,128,128,0.35);
     padding-bottom: 0.25rem; }
.subtitle { color: rgba(128,128,128,0.9); margin-top: 0; }
.verdict-box {
  border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem 0;
  border: 1px solid rgba(128,128,128,0.35);
}
.verdict-box.go-live { border-color: #12B886; background: rgba(18,184,134,0.08); }
.verdict-box.train-more { border-color: #E67700; background: rgba(230,119,0,0.08); }
.verdict-status { font-size: 1.2rem; font-weight: 700; margin: 0 0 0.25rem 0; }
.verdict-box.go-live .verdict-status { color: #0ca678; }
.verdict-box.train-more .verdict-status { color: #e67700; }
.sample-badge {
  display: inline-block; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.04em;
  border: 1px solid rgba(128,128,128,0.5); border-radius: 4px; padding: 0.1rem 0.4rem;
  margin-left: 0.5rem; vertical-align: middle;
}
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.35rem 0.6rem;
         border-bottom: 1px solid rgba(128,128,128,0.25); }
th { color: rgba(128,128,128,0.9); font-weight: 600; }
.chart { display: block; margin: 0.75rem 0; max-width: 100%; }
.axis { stroke: rgba(128,128,128,0.5); stroke-width: 1; }
.legend { font-size: 11px; fill: currentColor; }
.chart-title { font-size: 12px; fill: currentColor; font-weight: 600; }
.empty-msg { font-size: 12px; fill: rgba(128,128,128,0.8); }
.drawdown-area { fill: rgba(224,49,49,0.25); stroke: none; }
.drawdown-line { stroke: #E03131; }
.bar-label { font-size: 11px; fill: currentColor; }
.bar-value { font-size: 10px; fill: currentColor; }
.reasons { margin: 0.5rem 0 0 0; padding-left: 1.25rem; }
.caveats { font-size: 0.85rem; color: rgba(128,128,128,0.9); }
"""


def _render_verdict_box(verdict: Verdict, in_sample: bool) -> str:
    css_class = "go-live" if verdict.status == "GO-LIVE candidate" else "train-more"
    label = "IN-SAMPLE" if in_sample else "OUT-OF-SAMPLE"
    parts = [
        f'<div class="verdict-box {css_class}">',
        f'<p class="verdict-status">{_esc(verdict.status)}'
        f'<span class="sample-badge">{_esc(label)}</span></p>',
    ]
    if verdict.reasons:
        parts.append("<ul class=\"reasons\">")
        parts.extend(f"<li>{_esc(reason)}</li>" for reason in verdict.reasons)
        parts.append("</ul>")
    else:
        parts.append(
            "<p>All gates passed: data sufficiency, promotion floors, and risk-adjusted edge.</p>"
        )
    parts.append(
        f"<p>data_sufficient: {verdict.data_sufficient} &middot; "
        f"G2 (promotion floors): {'PASS' if verdict.g2_pass else 'FAIL'} &middot; "
        f"G3 (risk-adjusted edge): {'PASS' if verdict.g3_pass else 'FAIL'}</p>"
    )
    parts.append("</div>")
    return "\n".join(parts)


def _render_metrics_table(account_metrics: dict) -> str:
    rows = [
        f"<tr><td>{_esc(label)}</td><td>{_esc(_fmt_value(account_metrics[key]))}</td></tr>"
        for key, label in _METRIC_LABELS
        if key in account_metrics
    ]
    if not rows:
        return "<p>No account metrics available.</p>"
    return (
        "<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_gaps_table(gaps: list[GapItem]) -> str:
    if not gaps:
        return "<p>No deterministic gaps detected this run.</p>"
    rows = "".join(
        f"<tr><td>{_esc(gap.kind)}</td><td>{_esc(gap.evidence)}</td>"
        f"<td>{_esc(gap.recommendation)}</td></tr>"
        for gap in gaps
    )
    return (
        "<table><thead><tr><th>Kind</th><th>Evidence</th><th>Recommendation</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_html(
    sim: SimResult,
    benchmark: BenchmarkResult,
    verdict: Verdict,
    gaps: list[GapItem],
    account_metrics: dict,
    in_sample: bool = True,
) -> str:
    """Render the full self-contained HTML artifact for one simulation run.

    Mirrors `keel.sim.report.render_markdown`'s inputs and section order (verdict, account
    metrics, equity curve vs benchmark, drawdown, per-asset P&L, gap-analysis backlog) but as one
    HTML document with hand-emitted inline `<svg>` charts instead of Markdown tables/prose. No
    network, no file I/O, no clock reads -- the caller (the `simulate` CLI command) writes the
    returned string to disk.
    """
    per_asset_pnl: dict[str, Decimal] = account_metrics.get("per_asset_pnl", {}) or {}

    equity_svg = _svg_line(
        [
            ("Engine", sim.equity_curve, _LINE_COLORS[0]),
            (benchmark.name, benchmark.equity_curve, _LINE_COLORS[1]),
        ],
        title="Equity curve: engine vs. benchmark",
    )
    drawdown_svg = _svg_drawdown(sim.equity_curve)
    bars_svg = _svg_bars(per_asset_pnl)

    body = f"""
<h1>Engine Validation &amp; Trade-Simulation Artifact</h1>
<p class="subtitle">Sim Task 9 -- self-contained HTML/SVG companion to the Markdown report.</p>

<h2>Verdict</h2>
{_render_verdict_box(verdict, in_sample)}

<h2>Account metrics</h2>
{_render_metrics_table(account_metrics)}

<h2>Equity curve</h2>
{equity_svg}

<h2>Drawdown</h2>
{drawdown_svg}

<h2>Per-asset P&amp;L</h2>
{bars_svg}

<h2>Knowledge &amp; data gaps</h2>
{_render_gaps_table(gaps)}

<p class="caveats">In-sample run: treat results as an upper bound on edge, not a forward-looking
guarantee. See the accompanying Markdown report for the full caveats section.</p>
""".strip()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Engine Validation &amp; Trade-Simulation Artifact</title>
<style>{_CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""
