"""Tests for `keel.sim.artifact`: the self-contained HTML/SVG simulation artifact (Sim Task 9,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md` Task 9).

Every fixture is hand-built (constructed `SimResult`/`BenchmarkResult`/`Verdict`/`GapItem`
instances) so this stays a pure-function test -- no network, no real market data, no wall-clock
reads. The headline assertions guard the module's two hard constraints: the page is fully
self-contained (no `http://`/`https://` external resource references anywhere in the output) and
every chart is hand-emitted inline `<svg>` (no CDN chart library).
"""

from __future__ import annotations

from decimal import Decimal

from keel.sim.artifact import _svg_bars, _svg_drawdown, _svg_line, render_html
from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimResult, SimTelemetry
from keel.sim.report import GapItem, Verdict

_HOUR = 3600
_DAY = 86400


def _sim(equity_curve=None, contributions=None) -> SimResult:
    return SimResult(
        trades=[],
        equity_curve=equity_curve
        if equity_curve is not None
        else [
            (0, Decimal("0")),
            (_DAY, Decimal("500")),
            (2 * _DAY, Decimal("480")),
            (3 * _DAY, Decimal("620")),
        ],
        contributions=contributions if contributions is not None else [(0, Decimal("500"))],
        coverage={},
        telemetry=SimTelemetry(),
    )


def _benchmark() -> BenchmarkResult:
    return BenchmarkResult(
        name="dca_into_allowlist",
        equity_curve=[
            (0, Decimal("0")),
            (_DAY, Decimal("495")),
            (2 * _DAY, Decimal("500")),
            (3 * _DAY, Decimal("560")),
        ],
        contributions=[(0, Decimal("500"))],
        ending_value=Decimal("560"),
        total_return_pct=Decimal("0.12"),
        max_drawdown_pct=Decimal("0.05"),
        sharpe=Decimal("1.0"),
        sortino=Decimal("1.2"),
        return_per_drawdown=Decimal("2.4"),
    )


def _verdict(status: str = "GO-LIVE candidate") -> Verdict:
    return Verdict(
        status=status,
        reasons=[] if status == "GO-LIVE candidate" else ["risk-adjusted edge not established"],
        data_sufficient=True,
        g2_pass=True,
        g3_pass=status == "GO-LIVE candidate",
    )


def _gaps() -> list[GapItem]:
    return [
        GapItem(
            kind="unfed_cts_factor",
            evidence="CTS factor 'seasonality' was never present",
            recommendation="Wire it and backtest.",
        )
    ]


def _account_metrics() -> dict:
    return {
        "contributed": Decimal("500"),
        "ending_value": Decimal("620"),
        "net_pnl_usd": Decimal("120"),
        "total_return_pct": Decimal("0.24"),
        "max_drawdown_pct": Decimal("0.04"),
        "return_per_drawdown": Decimal("6"),
        "sharpe": Decimal("1.5"),
        "sortino": Decimal("1.8"),
        "trade_count": 3,
        "per_asset_pnl": {
            "BTC": Decimal("150.25"),
            "ETH": Decimal("-30.5"),
            "PAXG": Decimal("0"),
        },
    }


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_contains_svg_and_verdict_status():
    html = render_html(_sim(), _benchmark(), _verdict(), _gaps(), _account_metrics())
    assert "<svg" in html
    assert "GO-LIVE candidate" in html


def test_render_html_train_more_status_rendered():
    html = render_html(_sim(), _benchmark(), _verdict("TRAIN MORE"), _gaps(), _account_metrics())
    assert "TRAIN MORE" in html
    assert "risk-adjusted edge not established" in html


def test_render_html_has_no_external_resource_refs():
    html = render_html(_sim(), _benchmark(), _verdict(), _gaps(), _account_metrics())
    assert "http://" not in html
    assert "https://" not in html


def test_render_html_is_self_contained_document():
    html = render_html(_sim(), _benchmark(), _verdict(), _gaps(), _account_metrics())
    assert "<style" in html  # inline CSS, no <link rel="stylesheet">
    assert "<link" not in html
    assert "<script" not in html  # pure static markup -- no JS charting lib either


def test_render_html_in_sample_label():
    html = render_html(_sim(), _benchmark(), _verdict(), _gaps(), _account_metrics(), in_sample=True)
    assert "IN-SAMPLE" in html


def test_render_html_out_of_sample_label():
    html = render_html(
        _sim(), _benchmark(), _verdict(), _gaps(), _account_metrics(), in_sample=False
    )
    assert "OUT-OF-SAMPLE" in html


def test_render_html_includes_metrics_and_gap_table():
    html = render_html(_sim(), _benchmark(), _verdict(), _gaps(), _account_metrics())
    assert "unfed_cts_factor" in html
    assert "seasonality" in html
    # money values rendered from Decimal-formatted text, not raw float repr:
    assert "150.25" in html or "150.2" in html  # BTC per-asset pnl, formatted


def test_render_html_handles_empty_curves_and_gaps():
    empty_sim = _sim(equity_curve=[], contributions=[])
    empty_benchmark = BenchmarkResult(
        name="dca_into_allowlist",
        equity_curve=[],
        contributions=[],
        ending_value=Decimal("0"),
        total_return_pct=Decimal("0"),
        max_drawdown_pct=Decimal("0"),
        sharpe=Decimal("0"),
        sortino=Decimal("0"),
        return_per_drawdown=Decimal("0"),
    )
    html = render_html(empty_sim, empty_benchmark, _verdict(), [], {})
    assert "<svg" in html
    assert "GO-LIVE candidate" in html


# ---------------------------------------------------------------------------
# _svg_line
# ---------------------------------------------------------------------------


def test_svg_line_emits_two_polylines():
    series_list = [
        ("Engine", [(0, Decimal("100")), (_DAY, Decimal("110"))], "#4C6EF5"),
        ("Benchmark", [(0, Decimal("100")), (_DAY, Decimal("105"))], "#F76707"),
    ]
    svg = _svg_line(series_list)
    assert svg.count("<polyline") == 2
    assert "Engine" in svg and "Benchmark" in svg


def test_svg_line_empty_series_does_not_raise():
    svg = _svg_line([("Engine", [], "#4C6EF5")])
    assert "<svg" in svg


# ---------------------------------------------------------------------------
# _svg_drawdown
# ---------------------------------------------------------------------------


def test_svg_drawdown_renders_area_for_a_dip():
    equity_curve = [
        (0, Decimal("100")),
        (_DAY, Decimal("120")),
        (2 * _DAY, Decimal("90")),
        (3 * _DAY, Decimal("110")),
    ]
    svg = _svg_drawdown(equity_curve)
    assert "<svg" in svg
    assert "<polygon" in svg or "<path" in svg


def test_svg_drawdown_empty_curve_does_not_raise():
    svg = _svg_drawdown([])
    assert "<svg" in svg


# ---------------------------------------------------------------------------
# _svg_bars
# ---------------------------------------------------------------------------


def test_svg_bars_renders_one_bar_per_asset():
    per_asset_pnl = {"BTC": Decimal("150.25"), "ETH": Decimal("-30.5")}
    svg = _svg_bars(per_asset_pnl)
    assert svg.count("<rect") == 2
    assert "BTC" in svg and "ETH" in svg


def test_svg_bars_empty_dict_does_not_raise():
    svg = _svg_bars({})
    assert "<svg" in svg
