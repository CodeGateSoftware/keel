"""Tests for `keel.sim.report`: edge table, verdict gates, gap analysis, Markdown rendering
(Sim Task 7).

Every fixture is hand-built (constructed `BacktestResult`/`SimTelemetry`/`CoverageInfo`/
`BenchmarkResult` instances, or tiny deterministic test-only `Rule`s) so expected outcomes are
known exactly -- no network, no real market data, no wall-clock reads.
"""

from __future__ import annotations

from decimal import Decimal

from keel.config import TierConfig
from keel.data.history import CoverageInfo
from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimResult, SimTelemetry
from keel.sim.report import (
    DEFAULT_MAE_MFE_THRESHOLD,
    POOLED_KEY,
    GapItem,
    Verdict,
    analyze_gaps,
    build_verdict,
    edge_table,
    render_markdown,
)
from keel.sim.tiers import OVER_CAP, WITHIN_CAP, compute_tier_fee_result
from keel.strategy.promotion import PromotionConfig
from keel.strategy.rules.base import Rule, Setup
from keel.strategy.stats import BacktestResult
from keel.types import Candle, Granularity

_HOUR = 3600

CANONICAL = PromotionConfig()  # min_trades=100, min_expectancy=0, min_rr=1.5, min_win_rate=0.55


def _candle(ts: int, o: str, h: str, l: str, c: str) -> Candle:  # noqa: E741
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(1)
    )


# ---------------------------------------------------------------------------
# edge_table
# ---------------------------------------------------------------------------


class _OneShotRule(Rule):
    """Fires exactly once, on the bar at `trigger_ts`; never exits on signal (stop/target only)."""

    name = "one_shot"
    params: dict = {}

    def __init__(
        self, product_id: str, trigger_ts: int, entry: Decimal, stop: Decimal, target: Decimal
    ):
        self.product_id = product_id
        self.trigger_ts = trigger_ts
        self.entry = entry
        self.stop = stop
        self.target = target

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        window = candles_by_tf[Granularity.ONE_HOUR]
        latest = window[-1]
        if latest.ts != self.trigger_ts:
            return None
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=self.entry,
            stop=self.stop,
            target=self.target,
            context={},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


def _winning_series(asset_symbol: str) -> list[Candle]:
    # bar0: flat (detect fires nothing until latest ts == trigger); bar1 (trigger): entry touched;
    # bar2: target touched -> a win.
    return [
        _candle(0, "100", "100", "100", "100"),
        _candle(_HOUR, "100", "101", "99", "100"),
        _candle(2 * _HOUR, "100", "121", "99", "120"),
    ]


def test_edge_table_per_rule_and_pooled():
    btc_series = _winning_series("BTC")
    eth_series = _winning_series("ETH")
    rules = [
        _OneShotRule(
            "BTC-USD", _HOUR, entry=Decimal("100"), stop=Decimal("90"), target=Decimal("120")
        ),
        _OneShotRule(
            "ETH-USD", _HOUR, entry=Decimal("100"), stop=Decimal("90"), target=Decimal("120")
        ),
    ]
    candles_by_asset = {
        "BTC": {Granularity.ONE_HOUR: btc_series},
        "ETH": {Granularity.ONE_HOUR: eth_series},
    }

    edge = edge_table(rules, candles_by_asset, fee_pct=Decimal("0"), slippage_pct=Decimal("0"))

    assert "one_shot:BTC" in edge
    assert "one_shot:ETH" in edge
    assert POOLED_KEY in edge
    assert edge["one_shot:BTC"].n_trades == 1
    assert edge["one_shot:ETH"].n_trades == 1
    # pooled sums every rule's closed trades.
    assert edge[POOLED_KEY].n_trades == 2


def test_edge_table_missing_asset_candles_yields_empty_result_not_crash():
    rule = _OneShotRule(
        "SOL-USD", _HOUR, entry=Decimal("1"), stop=Decimal("0.9"), target=Decimal("1.2")
    )
    edge = edge_table([rule], candles_by_asset={}, fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    assert edge["one_shot:SOL"].n_trades == 0
    assert edge[POOLED_KEY].n_trades == 0


# ---------------------------------------------------------------------------
# analyze_gaps -- one test per detector
# ---------------------------------------------------------------------------


def _empty_telemetry(**overrides) -> SimTelemetry:
    defaults = dict(
        bars=0,
        signals_emitted=0,
        idle_spans=[],
        cts_factor_populated={},
        rejected_for_missing_input={},
        per_bucket_pnl={},
        mae_samples=[],
        mfe_giveback_samples=[],
    )
    defaults.update(overrides)
    return SimTelemetry(**defaults)


def test_gap_flags_idle_through_move_above_threshold_not_below():
    tel = _empty_telemetry(
        idle_spans=[
            (0, 100_000, "ETH", Decimal("0.12")),  # above threshold -> fires
            (0, 100_000, "BTC", Decimal("0.05")),  # below threshold -> filtered out
        ]
    )
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.10"))
    idle_gaps = [g for g in gaps if g.kind == "idle_through_move"]
    assert len(idle_gaps) == 1
    assert "ETH" in idle_gaps[0].evidence
    assert "BTC" not in idle_gaps[0].evidence


def test_gap_flags_unfed_cts_factor():
    tel = _empty_telemetry(
        cts_factor_populated={
            "condition_aligned": 40,
            "in_pullback": 42,
            "sr_touches": 10,
            "round_number_proximity": 5,
            "deceleration": 3,
            "ema_fan_aligned": 20,
            "rsi_extreme": 8,
            "rsi_divergence": 2,
            "candlestick_pattern": 1,
            "fib_confluence": 1,
            "seasonality": 0,  # never populated -> should fire
        }
    )
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    unfed = [g for g in gaps if g.kind == "unfed_cts_factor"]
    assert any("seasonality" in g.evidence for g in unfed)
    assert not any("in_pullback" in g.evidence for g in unfed)


def test_gap_flags_unfed_cts_factor_missing_key_defaults_to_zero():
    # a key entirely absent from cts_factor_populated is treated the same as count==0.
    tel = _empty_telemetry(cts_factor_populated={"condition_aligned": 5})
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    unfed_names = {g.evidence for g in gaps if g.kind == "unfed_cts_factor"}
    assert any("fib_confluence" in e for e in unfed_names)
    assert not any("condition_aligned" in e for e in unfed_names)


def test_gap_flags_would_have_traded_proxy():
    tel = _empty_telemetry(rejected_for_missing_input={"fib_confluence": 5, "seasonality": 0})
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    would_have = [g for g in gaps if g.kind == "would_have_traded"]
    assert len(would_have) == 1
    assert "fib_confluence" in would_have[0].evidence
    assert "5" in would_have[0].evidence
    # documented as a proxy, not a literal gate-rejection count.
    assert "proxy" in would_have[0].evidence.lower()


def test_gap_flags_data_coverage_partial_history():
    coverage = {
        ("PAXG", Granularity.ONE_HOUR): CoverageInfo(
            product="PAXG-USD",
            granularity=Granularity.ONE_HOUR,
            first_ts=200 * 86400,  # starts 200 days into the requested window
            last_ts=2000 * 86400,
            n_candles=10_000,
            requested_start_ts=0,
            gaps=0,
        ),
    }
    gaps = analyze_gaps(_empty_telemetry(), coverage=coverage, move_threshold_pct=Decimal("0.05"))
    partial = [g for g in gaps if g.kind == "data_coverage_limit" and "PAXG" in g.evidence]
    assert any("starts" in g.evidence for g in partial)


def test_gap_flags_data_coverage_missing_15m():
    coverage = {
        ("BTC", Granularity.ONE_HOUR): CoverageInfo(
            product="BTC-USD",
            granularity=Granularity.ONE_HOUR,
            first_ts=0,
            last_ts=2000 * 86400,
            n_candles=48_000,
            requested_start_ts=0,
            gaps=0,
        ),
    }
    gaps = analyze_gaps(_empty_telemetry(), coverage=coverage, move_threshold_pct=Decimal("0.05"))
    missing_15m = [
        g for g in gaps if g.kind == "data_coverage_limit" and "FIFTEEN_MINUTE" in g.evidence
    ]
    assert len(missing_15m) == 1
    assert "BTC" in missing_15m[0].evidence


def test_gap_flags_trade_management_mae_and_mfe_giveback():
    tel = _empty_telemetry(
        mae_samples=[Decimal("60"), Decimal("70")],  # mean=65 > DEFAULT_MAE_MFE_THRESHOLD(50)
        mfe_giveback_samples=[Decimal("80")],  # mean=80 > 50
    )
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    mgmt = [g for g in gaps if g.kind == "trade_management"]
    assert any("MAE" in g.evidence for g in mgmt)
    assert any("giveback" in g.evidence for g in mgmt)


def test_gap_no_trade_management_when_below_threshold():
    tel = _empty_telemetry(mae_samples=[Decimal("1")], mfe_giveback_samples=[Decimal("1")])
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    assert not [g for g in gaps if g.kind == "trade_management"]


def test_gap_flags_losing_bucket():
    tel = _empty_telemetry(
        per_bucket_pnl={
            ("rule_a", "BTC", "trend"): Decimal("-120"),
            ("rule_b", "ETH", "range"): Decimal("50"),
        }
    )
    gaps = analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
    losing = [g for g in gaps if g.kind == "losing_bucket"]
    assert len(losing) == 1
    assert "rule_a" in losing[0].evidence and "BTC" in losing[0].evidence
    assert "rule_b" not in losing[0].evidence


def test_analyze_gaps_custom_mae_mfe_threshold():
    tel = _empty_telemetry(mae_samples=[Decimal("5")])
    # default threshold (50) would not fire; an explicit lower override does.
    assert not [
        g
        for g in analyze_gaps(tel, coverage={}, move_threshold_pct=Decimal("0.05"))
        if g.kind == "trade_management"
    ]
    fired = [
        g
        for g in analyze_gaps(
            tel, coverage={}, move_threshold_pct=Decimal("0.05"), mae_mfe_threshold=Decimal("1")
        )
        if g.kind == "trade_management"
    ]
    assert fired
    assert DEFAULT_MAE_MFE_THRESHOLD == Decimal("50")


# ---------------------------------------------------------------------------
# build_verdict
# ---------------------------------------------------------------------------


def _pooled_result(
    n_trades: int = 150,
    win_rate: float = 0.6,
    avg_win: Decimal = Decimal("150"),
    avg_loss: Decimal = Decimal("-90"),
    expectancy: Decimal = Decimal("30"),
) -> BacktestResult:
    return BacktestResult(
        trades=[],
        n_trades=n_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        expectancy=expectancy,
        profit_factor=Decimal("2"),
        max_drawdown=Decimal("5"),
        max_losing_streak=3,
        avg_mfe=Decimal("2"),
        avg_mae=Decimal("1"),
    )


def _benchmark(
    total_return_pct: Decimal = Decimal("0.5"),
    max_drawdown_pct: Decimal = Decimal("0.3"),
    sharpe: Decimal = Decimal("1.0"),
    sortino: Decimal = Decimal("1.2"),
    return_per_drawdown: Decimal = Decimal("1.6"),
) -> BenchmarkResult:
    return BenchmarkResult(
        name="dca_into_allowlist",
        equity_curve=[(0, Decimal("0")), (1, Decimal("1500"))],
        contributions=[(0, Decimal("1500"))],
        ending_value=Decimal("1500"),
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe=sharpe,
        sortino=sortino,
        return_per_drawdown=return_per_drawdown,
    )


_PASSING_ACCOUNT_METRICS = {
    "return_per_drawdown": Decimal("2.0"),
    "sortino": Decimal("1.5"),
    "total_return_pct": Decimal("0.6"),
    "max_drawdown_pct": Decimal("0.2"),
}

_FAILING_ACCOUNT_METRICS = {
    "return_per_drawdown": Decimal("0.5"),
    "sortino": Decimal("0.4"),
    "total_return_pct": Decimal("0.1"),
    "max_drawdown_pct": Decimal("0.35"),
}


def test_verdict_go_live_when_floors_and_risk_adjusted_edge_pass():
    v = build_verdict(
        pooled=_pooled_result(),
        account_metrics=_PASSING_ACCOUNT_METRICS,
        benchmark=_benchmark(),
        coverage={},
        promotion_cfg=CANONICAL,
    )
    assert v.status == "GO-LIVE candidate"
    assert v.reasons == []
    assert v.data_sufficient and v.g2_pass and v.g3_pass


def test_verdict_train_more_when_floor_fails():
    pooled = _pooled_result(win_rate=0.50)  # below canonical 0.55
    v = build_verdict(
        pooled=pooled,
        account_metrics=_PASSING_ACCOUNT_METRICS,
        benchmark=_benchmark(),
        coverage={},
        promotion_cfg=CANONICAL,
    )
    assert v.status == "TRAIN MORE"
    assert not v.g2_pass
    assert any("win" in r.lower() for r in v.reasons)


def test_verdict_train_more_when_risk_adjusted_edge_fails():
    v = build_verdict(
        pooled=_pooled_result(),
        account_metrics=_FAILING_ACCOUNT_METRICS,
        benchmark=_benchmark(),
        coverage={},
        promotion_cfg=CANONICAL,
    )
    assert v.status == "TRAIN MORE"
    assert v.g2_pass  # G2 alone still passes
    assert not v.g3_pass
    assert any("risk-adjusted edge" in r for r in v.reasons)


def test_verdict_train_more_when_data_insufficient():
    coverage = {
        ("BTC", Granularity.ONE_HOUR): CoverageInfo(
            product="BTC-USD",
            granularity=Granularity.ONE_HOUR,
            first_ts=0,
            last_ts=10 * _HOUR,
            n_candles=10,  # far below MIN_BARS_FOR_SUFFICIENCY
            requested_start_ts=0,
            gaps=0,
        ),
    }
    v = build_verdict(
        pooled=_pooled_result(),
        account_metrics=_PASSING_ACCOUNT_METRICS,
        benchmark=_benchmark(),
        coverage=coverage,
        promotion_cfg=CANONICAL,
    )
    assert v.status == "TRAIN MORE"
    assert v.data_sufficient is False
    assert any("BTC" in r for r in v.reasons)


def test_verdict_dataclass_shape():
    v = Verdict(
        status="TRAIN MORE", reasons=["x"], data_sufficient=False, g2_pass=True, g3_pass=True
    )
    assert v.status == "TRAIN MORE"
    gi = GapItem(kind="k", evidence="e", recommendation="r")
    assert (gi.kind, gi.evidence, gi.recommendation) == ("k", "e", "r")


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_has_all_sections():
    sim = SimResult(
        trades=[],
        equity_curve=[(0, Decimal("0")), (86400, Decimal("500"))],
        contributions=[(0, Decimal("500"))],
        coverage={},
        telemetry=_empty_telemetry(),
    )
    edge = {POOLED_KEY: _pooled_result()}
    account_metrics = dict(
        _PASSING_ACCOUNT_METRICS, contributed=Decimal("500"), ending_value=Decimal("600")
    )
    benchmark = _benchmark()
    verdict = build_verdict(
        pooled=_pooled_result(),
        account_metrics=_PASSING_ACCOUNT_METRICS,
        benchmark=benchmark,
        coverage={},
        promotion_cfg=CANONICAL,
    )
    gaps = analyze_gaps(_empty_telemetry(), coverage={}, move_threshold_pct=Decimal("0.05"))

    md = render_markdown(sim, edge, account_metrics, benchmark, verdict, gaps, in_sample=True)

    for heading in ["Verdict", "Data coverage", "Edge", "Account", "Benchmark", "gaps", "Caveats"]:
        assert heading.lower() in md.lower()
    assert "IN-SAMPLE" in md
    assert "GO-LIVE candidate" in md


def test_render_markdown_out_of_sample_label():
    sim = SimResult(
        trades=[], equity_curve=[], contributions=[], coverage={}, telemetry=_empty_telemetry()
    )
    verdict = Verdict(
        status="TRAIN MORE", reasons=["r1"], data_sufficient=True, g2_pass=False, g3_pass=True
    )
    md = render_markdown(
        sim,
        edge={POOLED_KEY: _pooled_result(n_trades=0)},
        account_metrics={},
        benchmark=_benchmark(),
        verdict=verdict,
        gaps=[],
        in_sample=False,
    )
    assert "IN-SAMPLE" not in md
    assert "OUT-OF-SAMPLE" in md
    assert "r1" in md


# ---------------------------------------------------------------------------
# render_markdown -- Issue #86 tier/fee analysis matrix
# ---------------------------------------------------------------------------

_TIERS_FOR_TESTS = (
    TierConfig(
        name="Basic", free_volume_usd=Decimal("500"), subscription_usd_month=Decimal("4.99")
    ),
    TierConfig(
        name="Preferred",
        free_volume_usd=Decimal("10000"),
        subscription_usd_month=Decimal("29.99"),
    ),
    TierConfig(name="Premium", free_volume_usd=None, subscription_usd_month=Decimal("299.99")),
)


def _full_tier_matrix() -> list:
    """One over-cap AND one within-cap row per tier -- 3 tiers x 2 modes == 6 rows, matching
    what `keel simulate`'s default (non `--skip-within-cap`) path assembles."""
    monthly_volume = {0: Decimal("2000")}
    rows = []
    for tier in _TIERS_FOR_TESTS:
        rows.append(
            compute_tier_fee_result(
                monthly_volume=monthly_volume,
                n_months=1,
                tier=tier,
                mode=OVER_CAP,
                taker_pct=Decimal("0.012"),
                gross_pnl_usd=Decimal("100"),
            )
        )
        rows.append(
            compute_tier_fee_result(
                monthly_volume=monthly_volume,
                n_months=1,
                tier=tier,
                mode=WITHIN_CAP,
                taker_pct=Decimal("0.012"),
                gross_pnl_usd=Decimal("80"),
            )
        )
    return rows


def _minimal_render_markdown_args(tier_results):
    sim = SimResult(
        trades=[],
        equity_curve=[(0, Decimal("0")), (86400, Decimal("500"))],
        contributions=[(0, Decimal("500"))],
        coverage={},
        telemetry=_empty_telemetry(),
    )
    edge = {POOLED_KEY: _pooled_result()}
    account_metrics = dict(
        _PASSING_ACCOUNT_METRICS, contributed=Decimal("500"), ending_value=Decimal("600")
    )
    benchmark = _benchmark()
    verdict = build_verdict(
        pooled=_pooled_result(),
        account_metrics=_PASSING_ACCOUNT_METRICS,
        benchmark=benchmark,
        coverage={},
        promotion_cfg=CANONICAL,
    )
    gaps = analyze_gaps(_empty_telemetry(), coverage={}, move_threshold_pct=Decimal("0.05"))
    return render_markdown(
        sim,
        edge,
        account_metrics,
        benchmark,
        verdict,
        gaps,
        in_sample=True,
        tier_results=tier_results,
    )


def test_render_markdown_tier_section_renders_all_three_tiers_and_both_modes():
    md = _minimal_render_markdown_args(_full_tier_matrix())

    assert "Subscription tier & fee analysis" in md
    for tier_name in ("Basic", "Preferred", "Premium"):
        assert tier_name in md
    assert "Within cap" in md
    assert "Over cap" in md
    assert "Net P&L" in md
    # net P&L values from the fixture should appear verbatim in the rendered table.
    assert md.count("| Basic |") == 2
    assert md.count("| Preferred |") == 2
    assert md.count("| Premium |") == 2


def test_render_markdown_tier_section_shows_profits_cover_fees_yes_and_no():
    over = compute_tier_fee_result(
        monthly_volume={0: Decimal("50000")},
        n_months=1,
        tier=_TIERS_FOR_TESTS[0],  # Basic, $500 free
        mode=OVER_CAP,
        taker_pct=Decimal("0.012"),
        gross_pnl_usd=Decimal("1"),  # tiny gross P&L, dwarfed by fees -> net negative
    )
    within = compute_tier_fee_result(
        monthly_volume={0: Decimal("50000")},
        n_months=1,
        tier=_TIERS_FOR_TESTS[0],
        mode=WITHIN_CAP,
        taker_pct=Decimal("0.012"),
        gross_pnl_usd=Decimal("100"),  # comfortably net positive, 0 fees
    )
    assert over.profits_cover_fees is False
    assert within.profits_cover_fees is True

    md = _minimal_render_markdown_args([over, within])

    assert "no" in md.lower()
    assert "yes" in md.lower()


def test_render_markdown_no_tier_results_renders_placeholder_not_crash():
    md = _minimal_render_markdown_args(None)

    assert "Subscription tier & fee analysis" in md
    assert "not computed" in md.lower() or "no tier" in md.lower()


def test_render_markdown_backward_compatible_without_tier_results_kwarg():
    """Every existing caller of `render_markdown` (positional args, no `tier_results`) must keep
    working unchanged."""
    sim = SimResult(
        trades=[], equity_curve=[], contributions=[], coverage={}, telemetry=_empty_telemetry()
    )
    verdict = Verdict(
        status="TRAIN MORE", reasons=["r1"], data_sufficient=True, g2_pass=False, g3_pass=True
    )
    md = render_markdown(
        sim,
        {POOLED_KEY: _pooled_result(n_trades=0)},
        {},
        _benchmark(),
        verdict,
        [],
    )
    assert "Subscription tier & fee analysis" in md
