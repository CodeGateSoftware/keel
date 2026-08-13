"""Report, verdict & gap analysis for the offline simulator (Sim Task 7,
`docs/superpowers/plans/2026-07-17-engine-validation-simulation.md`, spec §6).

Pure functions, no I/O beyond *returning* strings -- nothing here reads a clock, touches the
network, or writes a file (that's the CLI's job, Task 8). Every date/threshold this module needs
is passed in by the caller.

**Module map:**
- `edge_table` -- reruns `strategy.backtest.backtest` per rule (Task 5.1's "edge pass"),
  keyed by `"{rule.name}:{asset}"`, plus a pooled `"__pooled__"` entry summarizing every rule's
  trades together.
- `build_verdict` -- the three gates from spec §6.2 (G1 data sufficiency, G2 promotion floors via
  `strategy.promotion.check_floors`, G3 risk-adjusted edge vs a `BenchmarkResult`).
- `analyze_gaps` -- the six deterministic "lacked information" detectors from spec §6.1, read
  straight off `portfolio_sim.SimTelemetry` (no LLM, no heuristic guessing -- these are counts and
  thresholds over data the sim already collected).
- `render_markdown` -- assembles the Task 6.1-of-spec report structure (verdict box, coverage,
  edge table, account results, benchmark comparison, gaps backlog, caveats) into one Markdown
  string.

**Interpretive notes** (mirroring the annotation style `portfolio_sim.py` already uses for
plan-prose specifics the plan leaves implicit):

- **G1 "min bar count"**: the plan says "each included asset >= a min bar count" without naming
  the number. `MIN_BARS_FOR_SUFFICIENCY` (module constant) is the threshold used here -- ~166
  days of hourly bars, enough to plausibly span more than one market regime. Assets whose
  `ONE_HOUR` coverage falls short are excluded from `data_sufficient`'s reasons and effectively
  ignored (but not hard-failed) as long as at least one asset still clears the bar; if *no* asset
  clears it, `data_sufficient` is `False`. When `coverage` is empty (the common case: portfolio_
  sim.run() always returns `coverage={}` and the CLI only attaches real `CoverageInfo` after the
  fact), there is nothing to gate on, so `data_sufficient` defaults to `True` rather than failing
  a report that simply wasn't handed coverage metadata.
- **G3 "materially lower max-DD" fallback**: the plan states the OR-branch as "comparable return
  at materially lower max-DD" without pinning numbers. `G3_COMPARABLE_RETURN_RATIO` (0.8) and
  `G3_LOWER_DRAWDOWN_RATIO` (0.7) implement that as: engine's total return >= 80% of the
  benchmark's, AND engine's max-DD <= 70% of the benchmark's.
- **`analyze_gaps`'s "would-have-traded" detector is a documented proxy** (per the plan's
  explicit instruction): `SimTelemetry.rejected_for_missing_input` is *not* a per-setup
  gate-rejection reason from the live engine -- `engine.py` doesn't expose one, and this module
  does not modify `engine.py` to add one. Per `portfolio_sim.py`'s own module docstring, that
  counter instead tallies, across every ENTER signal `evaluate()` ever emitted (opened or not),
  which CTS confluence factors were *absent* on that signal's context. It is a reasonable proxy
  for "the model was missing information that could have improved a real setup" but it is *not*
  literally "N setups were blocked from opening because of missing data" -- a rejected `can_open`
  in this sim is always a spend-cap rejection (see `sim/account.py`), never a confluence one. Do
  not read the evidence strings here as engine gate-rejection counts.
- **Trade-management threshold units**: `mae_samples`/`mfe_giveback_samples` are raw `Decimal`
  price-difference magnitudes as recorded by `portfolio_sim._process_held` (not normalized to
  entry price, %, or R-multiple, and not qty-scaled for the giveback samples specifically -- see
  that module's fields). Comparing a single absolute threshold across assets with very different
  price scales (e.g. BTC vs a small-cap) is a coarse heuristic, not a precise dollar figure;
  `DEFAULT_MAE_MFE_THRESHOLD` is a deliberately generic default and `analyze_gaps` accepts an
  override so a caller with per-asset context can pass something more meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from keel.execution.guards import _asset
from keel.sim.benchmark import BenchmarkResult
from keel.sim.portfolio_sim import SimResult, SimTelemetry
from keel.sim.tiers import OVER_CAP, WITHIN_CAP, TierFeeResult
from keel.strategy.backtest import _rule_trading_tf, backtest
from keel.strategy.indicators_cts import DEFAULT_WEIGHTS
from keel.strategy.promotion import PromotionConfig, check_floors, promotion_class_of
from keel.strategy.rules.base import Rule
from keel.strategy.stats import BacktestResult, summarize
from keel.types import Candle, Granularity

if TYPE_CHECKING:
    # Type-only: `sim` must not take a runtime dependency on `research`. The dependency
    # direction is research -> sim, never the reverse (spec §3).
    from keel.research.cscv import PBOResult

__all__ = [
    "DEFAULT_MAE_MFE_THRESHOLD",
    "G3_COMPARABLE_RETURN_RATIO",
    "G3_LOWER_DRAWDOWN_RATIO",
    "MIN_BARS_FOR_SUFFICIENCY",
    "POOLED_KEY",
    "GapItem",
    "Verdict",
    "analyze_gaps",
    "build_verdict",
    "edge_table",
    "group_trades_by_class",
    "render_markdown",
]

# G1: an asset's ONE_HOUR coverage below this many bars (~166 days) is excluded from the pooled
# verdict's "sufficient" set. See module docstring.
MIN_BARS_FOR_SUFFICIENCY = 4000

# G3 comparable-return / materially-lower-drawdown fallback ratios. See module docstring.
G3_COMPARABLE_RETURN_RATIO = Decimal("0.8")
G3_LOWER_DRAWDOWN_RATIO = Decimal("0.7")

# The canonical 11 CTS context keys (spec §9 / `indicators_cts.DEFAULT_WEIGHTS`), iterated in
# their declared order for the "unfed CTS factors" gap detector.
KNOWN_CTS_FACTORS: tuple[str, ...] = tuple(DEFAULT_WEIGHTS.keys())

# Default absolute-Decimal threshold for the trade-management gap detector. See module docstring
# ("Trade-management threshold units").
DEFAULT_MAE_MFE_THRESHOLD = Decimal("50")

# `edge_table`'s pooled entry key.
POOLED_KEY = "__pooled__"

# A coverage "first_ts starts this much later than requested" margin (30 days) before flagging
# partial history -- small pagination/inception-boundary slop shouldn't itself be a gap.
_PARTIAL_HISTORY_MARGIN_SEC = 30 * 86400
_SECONDS_PER_YEAR = 365 * 86400


@dataclass
class Verdict:
    """The report's headline call: GO-LIVE candidate or TRAIN MORE, and why."""

    status: str  # "GO-LIVE candidate" | "TRAIN MORE"
    reasons: list[str]  # failing-gate reasons; empty iff status == "GO-LIVE candidate"
    data_sufficient: bool
    g2_pass: bool
    g3_pass: bool


@dataclass
class GapItem:
    """One row of the "knowledge & data gaps -> training backlog" section."""

    kind: str
    evidence: str
    recommendation: str


# ---------------------------------------------------------------------------
# Edge table
# ---------------------------------------------------------------------------


def edge_table(
    rules: list[Rule],
    candles_by_asset: dict[str, dict[Granularity, list[Candle]]],
    fee_pct: Decimal,
    slippage_pct: Decimal,
) -> dict[str, BacktestResult]:
    """Backtest every rule in `rules` over its own asset's native-timeframe series, plus a pooled
    entry.

    Each `rule` is bound to one asset via `rule.product_id` (matching `portfolio_sim.run`'s
    convention). A rule is backtested on the series for ITS OWN trading timeframe
    (`_rule_trading_tf` -- `ONE_HOUR` for the hourly rules, `ONE_DAY` for the daily-native
    `TurtleBreakout`), with the `ONE_HOUR` series passed as `finer_candles` for intrabar
    stop/target resolution when the rule's own timeframe is coarser. Results are keyed
    `"{rule.name}:{asset}"` (not bare `rule.name`) so two rules of the same kind bound to
    different assets don't collide. `POOLED_KEY` (`"__pooled__"`) holds
    `strategy.stats.summarize()` over every rule's trades concatenated -- the pooled sample
    `build_verdict`'s G2 gate is checked against.

    A rule whose asset has no cached candles for its timeframe gets an empty series (an all-zero
    `BacktestResult`, `n_trades=0`) rather than raising -- absent data is a data-coverage gap
    (see `analyze_gaps`), not a crash.
    """
    results: dict[str, BacktestResult] = {}
    pooled_trades = []

    for rule in rules:
        asset = _asset(rule.product_id)
        tf = _rule_trading_tf(rule)
        per_tf = candles_by_asset.get(asset, {})
        series = per_tf.get(tf, [])
        finer = per_tf.get(Granularity.ONE_HOUR, []) if tf != Granularity.ONE_HOUR else None
        result = backtest(
            rule, series, finer_candles=finer, fee_pct=fee_pct, slippage_pct=slippage_pct
        )
        results[f"{rule.name}:{asset}"] = result
        pooled_trades.extend(result.trades)

    results[POOLED_KEY] = summarize(pooled_trades)
    return results


def group_trades_by_class(
    edge: dict[str, BacktestResult], rules: list[Rule]
) -> dict[str, BacktestResult]:
    """Pool each rule's edge-pass trades by promotion class -> summarized `BacktestResult`.

    Keyed by `promotion.promotion_class_of(rule)`. This is the per-class sample
    `build_verdict`'s G2 gate checks against each class's own floor (KB §25.5): a
    low-win/high-R:R trend-follower is judged by the trend floor rather than the global one,
    while other classes keep the canonical floor. `edge` is `edge_table`'s output; its
    per-rule keys are `"{rule.name}:{asset}"` (the `POOLED_KEY` entry is ignored -- it pools
    across *all* classes and so isn't meaningful per-class). A rule whose edge entry is
    missing is skipped (absent data is a coverage gap, not a crash -- mirrors `edge_table`).
    """
    trades_by_class: dict[str, list] = {}
    for rule in rules:
        result = edge.get(f"{rule.name}:{_asset(rule.product_id)}")
        if result is None:
            continue
        trades_by_class.setdefault(promotion_class_of(rule), []).extend(result.trades)
    return {cls: summarize(trades) for cls, trades in trades_by_class.items()}


# ---------------------------------------------------------------------------
# Verdict (spec §6.2)
# ---------------------------------------------------------------------------


def _g1_data_sufficiency(coverage: dict) -> tuple[bool, list[str]]:
    """G1: at least one included asset must clear `MIN_BARS_FOR_SUFFICIENCY` `ONE_HOUR` bars.

    `coverage` is expected in `data/history.py::ensure_history`'s return shape --
    `dict[tuple[str, Granularity], CoverageInfo]` -- but any mapping exposing `.n_candles` per
    `(asset, Granularity.ONE_HOUR)` key works. An empty/missing `coverage` is treated as "nothing
    to gate on" (sufficient), not a failure -- see module docstring.
    """
    hourly = {
        asset: info
        for (asset, granularity), info in coverage.items()
        if granularity == Granularity.ONE_HOUR
    }
    if not hourly:
        return True, []

    insufficient = {
        asset: info for asset, info in hourly.items() if info.n_candles < MIN_BARS_FOR_SUFFICIENCY
    }
    sufficient_count = len(hourly) - len(insufficient)
    reasons = [
        f"{asset}: only {info.n_candles} hourly bars (< {MIN_BARS_FOR_SUFFICIENCY} min) -- "
        "excluded from the pooled verdict"
        for asset, info in sorted(insufficient.items())
    ]
    return sufficient_count > 0, reasons


def _g2_per_class(
    pooled_by_class: dict[str, BacktestResult],
    floors: dict[str, PromotionConfig],
    default_cfg: PromotionConfig,
) -> tuple[bool, list[str]]:
    """G2 evaluated per rule class (KB §25.5): each class's pooled sample must clear its own
    floor. A class absent from `floors` falls back to `default_cfg`. Failing reasons are
    prefixed with `[class]` so a mixed run makes clear which class fell short. An empty
    grouping (no rules produced any trades to classify) fails rather than passing vacuously.
    """
    if not pooled_by_class:
        return False, ["no rule classes with trades to evaluate against promotion floors"]
    reasons: list[str] = []
    for cls in sorted(pooled_by_class):
        floor = floors.get(cls, default_cfg)
        _ok, cls_reasons = check_floors(pooled_by_class[cls], floor)
        reasons.extend(f"[{cls}] {reason}" for reason in cls_reasons)
    return (len(reasons) == 0, reasons)


def build_verdict(
    pooled: BacktestResult,
    account_metrics: dict,
    benchmark: BenchmarkResult,
    coverage: dict,
    promotion_cfg: PromotionConfig,
    pooled_by_class: dict[str, BacktestResult] | None = None,
    floors: dict[str, PromotionConfig] | None = None,
) -> Verdict:
    """The three-gate GO-LIVE/TRAIN-MORE call (spec §6.2). All failing reasons are surfaced.

    `account_metrics` is a plain `dict` (built by the caller from `sim.metrics.*` over the dollar
    account pass -- see `docs/superpowers/specs/2026-07-17-engine-validation-simulation-design.md`
    §5.2); this function reads `"return_per_drawdown"`, `"sortino"`, `"total_return_pct"`, and
    `"max_drawdown_pct"` from it (missing keys default to `Decimal(0)`, which fails G3
    conservatively rather than raising).

    **G2 (promotion floors)** is evaluated per rule *class* when `pooled_by_class` is supplied
    (from `group_trades_by_class`): each class's pooled trades must clear its own floor from
    `floors` (falling back to `promotion_cfg` for any class not listed), so a low-win/high-R:R
    trend-follower is judged by the trend floor, not the global one (KB §25.5). When
    `pooled_by_class` is `None`, G2 falls back to checking the single all-rules `pooled` sample
    against `promotion_cfg` -- the original behaviour, kept so existing callers are unaffected.
    """
    reasons: list[str] = []

    data_sufficient, g1_reasons = _g1_data_sufficiency(coverage)
    if not data_sufficient:
        reasons.extend(
            g1_reasons or ["no asset has sufficient ONE_HOUR bar coverage for a meaningful sample"]
        )

    if pooled_by_class is not None:
        g2_pass, g2_reasons = _g2_per_class(pooled_by_class, floors or {}, promotion_cfg)
    else:
        g2_pass, g2_reasons = check_floors(pooled, promotion_cfg)
    if not g2_pass:
        reasons.extend(g2_reasons)

    account_rpd = account_metrics.get("return_per_drawdown", Decimal(0))
    account_sortino = account_metrics.get("sortino", Decimal(0))
    account_return = account_metrics.get("total_return_pct", Decimal(0))
    account_dd = account_metrics.get("max_drawdown_pct", Decimal(0))

    risk_adjusted_edge = (
        account_rpd >= benchmark.return_per_drawdown and account_sortino >= benchmark.sortino
    )
    comparable_return_lower_dd = (
        account_return >= G3_COMPARABLE_RETURN_RATIO * benchmark.total_return_pct
        and account_dd <= G3_LOWER_DRAWDOWN_RATIO * benchmark.max_drawdown_pct
    )
    g3_pass = risk_adjusted_edge or comparable_return_lower_dd
    if not g3_pass:
        reasons.append(
            "risk-adjusted edge not established vs benchmark: "
            f"return/DD {account_rpd} < {benchmark.return_per_drawdown} or "
            f"Sortino {account_sortino} < {benchmark.sortino} "
            f"(comparable-return/lower-DD fallback also failed: return {account_return} < "
            f"{G3_COMPARABLE_RETURN_RATIO} x benchmark return {benchmark.total_return_pct}, or "
            f"drawdown {account_dd} > {G3_LOWER_DRAWDOWN_RATIO} x benchmark drawdown "
            f"{benchmark.max_drawdown_pct})"
        )

    status = "GO-LIVE candidate" if (data_sufficient and g2_pass and g3_pass) else "TRAIN MORE"
    return Verdict(
        status=status,
        reasons=reasons,
        data_sufficient=data_sufficient,
        g2_pass=g2_pass,
        g3_pass=g3_pass,
    )


# ---------------------------------------------------------------------------
# Gap analysis (spec §6.1)
# ---------------------------------------------------------------------------


def _idle_through_move_gaps(telemetry: SimTelemetry, move_threshold_pct: Decimal) -> list[GapItem]:
    gaps = []
    for start_ts, end_ts, asset, move_pct in telemetry.idle_spans:
        if move_pct < move_threshold_pct:
            continue
        gaps.append(
            GapItem(
                kind="idle_through_move",
                evidence=(
                    f"{asset}: no rule fired from ts={start_ts} to ts={end_ts} "
                    f"while price moved {move_pct:.2%}"
                ),
                recommendation=(
                    "No existing rule covers this span's regime/phase -- inspect it "
                    "(e.g. strong-trend-no-pullback, parabolic-blowoff) and implement + backtest "
                    "the deferred macro-cycle / trailing-exit knowledge for this asset."
                ),
            )
        )
    return gaps


def _unfed_cts_factor_gaps(telemetry: SimTelemetry) -> list[GapItem]:
    gaps = []
    for name in KNOWN_CTS_FACTORS:
        if telemetry.cts_factor_populated.get(name, 0) != 0:
            continue
        gaps.append(
            GapItem(
                kind="unfed_cts_factor",
                evidence=(
                    f"CTS factor '{name}' was never present (0 populated occurrences this run)"
                ),
                recommendation=(
                    f"Factor '{name}' contributed 0 signal all run -- wire it and backtest, "
                    "or drop it from the scorer."
                ),
            )
        )
    return gaps


def _would_have_traded_gaps(telemetry: SimTelemetry) -> list[GapItem]:
    """Proxy detector -- see module docstring for why this is not literal gate-rejection data."""
    gaps = []
    for name, count in sorted(telemetry.rejected_for_missing_input.items()):
        if count <= 0:
            continue
        gaps.append(
            GapItem(
                kind="would_have_traded",
                evidence=(
                    f"{count} evaluated ENTER signals had confluence input '{name}' absent "
                    "(proxy metric: CTS keys absent from emitted setups, not literal engine "
                    "gate-rejections -- see analyze_gaps' docstring)"
                ),
                recommendation=(
                    f"{count} occurrences missing confluence input '{name}' -- prioritize "
                    "wiring/backtesting that data or feature."
                ),
            )
        )
    return gaps


def _data_coverage_gaps(coverage: dict) -> list[GapItem]:
    gaps: list[GapItem] = []
    if not coverage:
        return gaps

    assets = sorted({asset for asset, _granularity in coverage})
    for asset in assets:
        one_hour = coverage.get((asset, Granularity.ONE_HOUR))
        if (
            one_hour is not None
            and one_hour.first_ts is not None
            and one_hour.first_ts > one_hour.requested_start_ts + _PARTIAL_HISTORY_MARGIN_SEC
        ):
            span_years = Decimal(one_hour.first_ts - one_hour.requested_start_ts) / Decimal(
                _SECONDS_PER_YEAR
            )
            gaps.append(
                GapItem(
                    kind="data_coverage_limit",
                    evidence=(
                        f"{asset}: history starts {span_years:.1f}yr into the requested window "
                        f"(first_ts={one_hour.first_ts}, requested_start_ts="
                        f"{one_hour.requested_start_ts})"
                    ),
                    recommendation=(
                        f"{asset} has partial history -- its edge-table sample is shorter than "
                        "the nominal window; re-pull as more history accrues."
                    ),
                )
            )

        fifteen_min = coverage.get((asset, Granularity.FIFTEEN_MINUTE))
        if fifteen_min is None or fifteen_min.n_candles == 0:
            gaps.append(
                GapItem(
                    kind="data_coverage_limit",
                    evidence=f"{asset}: no FIFTEEN_MINUTE candles cached",
                    recommendation=(
                        f"Pull 15m candles for {asset} to sharpen intrabar stop-vs-target "
                        "resolution (coarse hourly-only fallback is used otherwise)."
                    ),
                )
            )
    return gaps


def _trade_management_gaps(telemetry: SimTelemetry, threshold: Decimal) -> list[GapItem]:
    gaps = []
    if telemetry.mae_samples:
        mean_mae = sum(telemetry.mae_samples, Decimal(0)) / len(telemetry.mae_samples)
        if mean_mae > threshold:
            gaps.append(
                GapItem(
                    kind="trade_management",
                    evidence=(
                        f"mean MAE across {len(telemetry.mae_samples)} closed trades = "
                        f"{mean_mae} (> {threshold})"
                    ),
                    recommendation=(
                        "Stops are systematically wide relative to typical adverse excursion -- "
                        "tighten stop placement or add a time-based invalidation rule; "
                        "re-backtest."
                    ),
                )
            )
    if telemetry.mfe_giveback_samples:
        mean_giveback = sum(telemetry.mfe_giveback_samples, Decimal(0)) / len(
            telemetry.mfe_giveback_samples
        )
        if mean_giveback > threshold:
            gaps.append(
                GapItem(
                    kind="trade_management",
                    evidence=(
                        f"mean MFE giveback across {len(telemetry.mfe_giveback_samples)} closed "
                        f"trades = {mean_giveback} (> {threshold})"
                    ),
                    recommendation=(
                        "Winners are giving back a large share of their favorable excursion -- "
                        "add a trailing-stop / partial-exit management rule; backtest against "
                        "current exits."
                    ),
                )
            )
    return gaps


def _losing_bucket_gaps(telemetry: SimTelemetry) -> list[GapItem]:
    gaps = []
    for (rule_kind, asset, regime_name), pnl in sorted(telemetry.per_bucket_pnl.items()):
        if pnl >= 0:
            continue
        gaps.append(
            GapItem(
                kind="losing_bucket",
                evidence=(
                    f"bucket (rule={rule_kind}, asset={asset}, regime={regime_name}) "
                    f"summed pnl = {pnl}"
                ),
                recommendation=(
                    f"Bucket (rule={rule_kind}, asset={asset}, regime={regime_name}) loses "
                    "consistently -- retune entry/exit for that regime or demote the rule there; "
                    "gather more samples before re-testing."
                ),
            )
        )
    return gaps


def analyze_gaps(
    telemetry: SimTelemetry,
    coverage: dict,
    move_threshold_pct: Decimal,
    mae_mfe_threshold: Decimal = DEFAULT_MAE_MFE_THRESHOLD,
) -> list[GapItem]:
    """Run the six deterministic gap detectors from spec §6.1 over one sim run's telemetry.

    On `TRAIN MORE` this list is the roadmap; on `GO-LIVE` it's the hardening backlog (spec §6.1).
    See the module docstring for the "would-have-traded" proxy caveat and the trade-management
    threshold's unit caveat -- both apply to this function's output.

    `move_threshold_pct` re-filters `telemetry.idle_spans` at report time (spans are recorded by
    `portfolio_sim.run()` against its own `MOVE_THRESHOLD_PCT`; a caller here can apply an equal
    or stricter cutoff without re-running the sim). `mae_mfe_threshold` overrides
    `DEFAULT_MAE_MFE_THRESHOLD` for the trade-management detector.
    """
    gaps: list[GapItem] = []
    gaps.extend(_idle_through_move_gaps(telemetry, move_threshold_pct))
    gaps.extend(_unfed_cts_factor_gaps(telemetry))
    gaps.extend(_would_have_traded_gaps(telemetry))
    gaps.extend(_data_coverage_gaps(coverage))
    gaps.extend(_trade_management_gaps(telemetry, mae_mfe_threshold))
    gaps.extend(_losing_bucket_gaps(telemetry))
    return gaps


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_ACCOUNT_METRIC_LABELS: list[tuple[str, str]] = [
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
]


def _render_verdict_section(verdict: Verdict, in_sample: bool) -> list[str]:
    label = "IN-SAMPLE" if in_sample else "OUT-OF-SAMPLE"
    lines = ["## Verdict", "", f"**{verdict.status}** ({label})", ""]
    if verdict.reasons:
        lines.append("Failing gates:")
        lines.extend(f"- {reason}" for reason in verdict.reasons)
    else:
        lines.append(
            "All gates passed: data sufficiency, promotion floors, and risk-adjusted edge."
        )
    lines.append("")
    lines.append(f"- data_sufficient: {verdict.data_sufficient}")
    lines.append(f"- G2 (promotion floors): {'PASS' if verdict.g2_pass else 'FAIL'}")
    lines.append(f"- G3 (risk-adjusted edge): {'PASS' if verdict.g3_pass else 'FAIL'}")
    return lines


def _render_coverage_section(sim: SimResult) -> list[str]:
    lines = ["## Data coverage", ""]
    coverage = sim.coverage or {}
    if not coverage:
        lines.append(
            "_No coverage metadata attached to this run (`portfolio_sim.run()` always returns "
            "`coverage={}`; the CLI attaches per-asset `CoverageInfo` from `data/history.py` "
            "before rendering)._"
        )
        return lines

    lines.append("| Asset | Granularity | First ts | Last ts | Candles | Gaps |")
    lines.append("|---|---|---|---|---|---|")
    for (asset, granularity), info in sorted(
        coverage.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
    ):
        lines.append(
            f"| {asset} | {granularity.value} | {info.first_ts} | {info.last_ts} | "
            f"{info.n_candles} | {info.gaps} |"
        )
    return lines


def _render_edge_section(
    edge: dict[str, BacktestResult], fee_pct: Decimal | None = None
) -> list[str]:
    """The edge table, with the fee its numbers were priced at stated above it.

    The fee line is not optional furniture. A profit factor is a claim about NET edge, and for a
    strategy whose per-trade move is the same order as its costs, the fee is the dominant term --
    #247 found every printed figure here priced at the maker rate (0.006) under a taker fill
    model, which moved profit factors by up to 0.31 and was invisible because the number was
    never printed beside them. `fee_pct=None` renders "not recorded" rather than omitting the
    line, so a caller that forgets to pass it produces a visible gap instead of a clean-looking
    table.
    """
    fee_note = (
        f"Fills priced at **{fee_pct * 100:.4f}%** per leg (taker) plus slippage."
        if fee_pct is not None
        else "**Fee rate not recorded for this run** -- these figures cannot be compared "
        "against runs priced differently."
    )
    lines = [
        "## Edge table",
        "",
        "Per-rule and pooled backtest stats (unit-less R-multiples). "
        f"`{POOLED_KEY}` is the pooled sample G2 is checked against.",
        "",
        fee_note,
        "",
        "| Rule | N | Win% | Expectancy | Avg win | Avg loss | Profit factor | Max DD | "
        "Losing streak | Avg MFE | Avg MAE |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    ordered_keys = [key for key in edge if key != POOLED_KEY]
    if POOLED_KEY in edge:
        ordered_keys.append(POOLED_KEY)
    for key in ordered_keys:
        result = edge[key]
        label = f"**{key}**" if key == POOLED_KEY else key
        lines.append(
            f"| {label} | {result.n_trades} | {result.win_rate:.1%} | {result.expectancy} | "
            f"{result.avg_win} | {result.avg_loss} | {result.profit_factor} | "
            f"{result.max_drawdown} | {result.max_losing_streak} | {result.avg_mfe} | "
            f"{result.avg_mae} |"
        )
    return lines


def _render_account_section(account_metrics: dict) -> list[str]:
    lines = ["## Account results", "", "| Metric | Value |", "|---|---|"]
    for key, label in _ACCOUNT_METRIC_LABELS:
        if key not in account_metrics:
            continue
        lines.append(f"| {label} | {account_metrics[key]} |")

    per_asset = account_metrics.get("per_asset_pnl")
    if per_asset:
        lines.append("")
        lines.append("Per-asset P&L:")
        lines.extend(f"- {asset}: {pnl}" for asset, pnl in sorted(per_asset.items()))
    return lines


def _render_benchmark_section(account_metrics: dict, benchmark: BenchmarkResult) -> list[str]:
    lines = [
        "## Benchmark comparison",
        "",
        f"Engine vs `{benchmark.name}`:",
        "",
        "| Metric | Engine | Benchmark |",
        "|---|---|---|",
    ]
    rows = [
        (
            "Total return",
            account_metrics.get("total_return_pct", Decimal(0)),
            benchmark.total_return_pct,
        ),
        (
            "Max drawdown",
            account_metrics.get("max_drawdown_pct", Decimal(0)),
            benchmark.max_drawdown_pct,
        ),
        ("Sharpe", account_metrics.get("sharpe", Decimal(0)), benchmark.sharpe),
        ("Sortino", account_metrics.get("sortino", Decimal(0)), benchmark.sortino),
        (
            "Return / drawdown",
            account_metrics.get("return_per_drawdown", Decimal(0)),
            benchmark.return_per_drawdown,
        ),
    ]
    lines.extend(
        f"| {label} | {engine_val} | {bench_val} |" for label, engine_val, bench_val in rows
    )
    return lines


_TIER_MODE_LABELS: dict[str, str] = {WITHIN_CAP: "Within cap", OVER_CAP: "Over cap"}


def _render_tier_section(tier_results: list[TierFeeResult]) -> list[str]:
    """"Subscription tier & fee analysis" (Issue #86) -- one row per (tier, mode) in
    `tier_results`: does staying WITHIN a Coinbase One tier's fee-free monthly trading-volume
    allowance, or trading freely and paying the taker fee OVER it, net out ahead once the tier's
    own subscription cost is subtracted too? See `sim.tiers`' module docstring for the
    fee-layering interpretation `fees_usd` here represents."""
    lines = [
        "## Subscription tier & fee analysis",
        "",
        "For each Coinbase One tier: staying WITHIN the fee-free monthly trading-volume "
        "allowance (a throttled run, 0 trading fees, subscription still due) vs trading freely "
        "and paying the taker fee on volume EXCEEDING it (\"over cap\"). Premium's allowance is "
        "unlimited, so its within-cap and over-cap rows are identical.",
        "",
    ]
    if not tier_results:
        lines.append("_No tier/fee analysis was computed for this run._")
        return lines

    lines.append(
        "| Tier | Mode | Total volume | Free volume | Paid volume | Trading fees | "
        "Subscription | Gross P&L | Net P&L | Profits cover fees? |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in tier_results:
        mode_label = _TIER_MODE_LABELS.get(r.mode, r.mode)
        lines.append(
            f"| {r.tier_name} | {mode_label} | {r.total_volume_usd} | {r.free_volume_usd} | "
            f"{r.paid_volume_usd} | {r.fees_usd} | {r.subscription_usd} | {r.gross_pnl_usd} | "
            f"{r.net_pnl_usd} | {'yes' if r.profits_cover_fees else 'no'} |"
        )

    best = max(tier_results, key=lambda r: r.net_pnl_usd)
    lines.append("")
    lines.append(
        f"**Takeaway:** {best.tier_name} ({_TIER_MODE_LABELS.get(best.mode, best.mode).lower()}) "
        f"nets the best outcome of this matrix, at {best.net_pnl_usd} net P&L."
    )
    return lines


def _render_gaps_section(gaps: list[GapItem]) -> list[str]:
    lines = ["## Knowledge & data gaps -> training backlog", ""]
    if not gaps:
        lines.append("No deterministic gaps detected this run.")
        return lines
    lines.append("| Kind | Evidence | Recommendation |")
    lines.append("|---|---|---|")
    for gap in gaps:
        evidence = gap.evidence.replace("|", "\\|")
        recommendation = gap.recommendation.replace("|", "\\|")
        lines.append(f"| {gap.kind} | {evidence} | {recommendation} |")
    return lines


def _render_caveats_section() -> list[str]:
    return [
        "## Caveats",
        "",
        "- **In-sample**: this run has no holdout period; treat results as an upper bound on "
        "edge, not a forward-looking guarantee.",
        "- **USDC stand-in**: USD-quoted candle history stands in for USDC pairs (Coinbase's "
        "candle history is USD-denominated); assumed 1:1.",
        "- **Money-management ramp not modeled**: the Phase-4 profit-triggered sizing "
        "acceleration is not simulated here -- plain fixed-fractional sizing is used throughout.",
        "- **PAXG partial history**: PAXG's candle history is shorter than the other assets' "
        "requested window; its edge-table and account-pass samples are correspondingly smaller.",
        "- Even on a GO-LIVE verdict, run the supervised tiny-cap confirm-mode test before "
        "committing real capital.",
    ]


def _render_pbo_section(
    result: PBOResult, gate_ok: bool | None, gate_reasons: list[str]
) -> list[str]:
    """Render the PBO block. The reading rule travels WITH the number, deliberately.

    §78.7's limitation 4 means a bare PBO is misleading on its own for exactly the plateau
    shape this project is told to prefer, so the section never prints phi without the
    degradation slope beside it and the interpretation underneath.

    `gate_ok=None` means the thresholds were never applied to these numbers, and renders as
    NOT EVALUATED rather than PASS. It used to default to `True` -- a section that printed
    "G4: PASS" for a gate nobody had run, which is the same defect #247 fixed in the promotion
    gate itself, in the module that reports on it.
    """
    status = "NOT EVALUATED" if gate_ok is None else ("PASS" if gate_ok else "FAIL")
    lines = [
        "## Overfitting diagnostics (PBO / CSCV)",
        "",
        f"- **PBO (phi):** {result.pbo:.4f}",
        f"- **Degradation slope:** {result.degradation_slope:.4f}",
        f"- **Prob[OOS < 0]:** {result.prob_loss:.4f}",
        f"- **Stochastic dominance:** 1st-order {result.dominance_1st}, "
        f"2nd-order {result.dominance_2nd}",
        f"- **Grid:** N={result.n_columns} columns, S={result.n_blocks} blocks, "
        f"{result.n_combinations} combinations, {result.rows_used} rows "
        f"({result.rows_dropped} oldest dropped)",
        "",
        f"**G4: {status}**",
        "",
    ]
    for reason in gate_reasons:
        lines.append(f"- {reason}")
    if gate_reasons:
        lines.append("")
    lines.extend(
        [
            "> Read PBO alongside the degradation slope, never alone (KB §78.7 limitation 4): "
            "a broad plateau of near-identical configurations produces a high PBO **by "
            "construction**, and a broad plateau is what §54.10/§73.13 tell us to prefer. "
            "High PBO with a flat, positive OOS scatter is the good outcome; high PBO with a "
            "steeply negative slope is the bad one.",
            "",
            "> `Prob[OOS < 0]` is reported **separately** from PBO on purpose (§78.8): even at "
            "phi near 0 it can be high, meaning OOS performance is poor for reasons other than "
            "overfitting. They are distinct failure modes.",
            "",
            "> PBO is orthogonal to look-ahead bias and fee realism (§78.7 limitation 3). It "
            "does not check whether the backtest itself is correct, and it does not retire the "
            "OOS firewall (§54.10) -- both are needed.",
            "",
        ]
    )
    return lines


def render_markdown(
    sim: SimResult,
    edge: dict[str, BacktestResult],
    account_metrics: dict,
    benchmark: BenchmarkResult,
    verdict: Verdict,
    gaps: list[GapItem],
    in_sample: bool = True,
    tier_results: list[TierFeeResult] | None = None,
    pbo_result: PBOResult | None = None,
    pbo_gate: tuple[bool, list[str]] | None = None,
    fee_pct: Decimal | None = None,
) -> str:
    """Render the full report (spec §6 structure, plus Issue #86's tier/fee matrix) as one
    Markdown string.

    Section order: verdict box, data coverage, edge table, account results, benchmark comparison,
    subscription tier & fee analysis, knowledge & data gaps, caveats. Pure string assembly -- no
    file I/O (the CLI, Task 8, writes the result to `--out`).

    `tier_results` (Issue #86) is optional and defaults to an empty matrix (renders a
    "not computed" placeholder) so every existing caller of this function keeps working
    unchanged. `pbo_result`/`pbo_gate` (KB §78) follow the same backward-compatible pattern:
    the overfitting section is emitted only when a PBO run was actually performed.
    """
    sections = [
        _render_verdict_section(verdict, in_sample),
        _render_coverage_section(sim),
        _render_edge_section(edge, fee_pct),
        _render_account_section(account_metrics),
        _render_benchmark_section(account_metrics, benchmark),
        _render_tier_section(tier_results or []),
        _render_gaps_section(gaps),
        _render_caveats_section(),
    ]
    if pbo_result is not None:
        gate_ok, gate_reasons = (
            pbo_gate
            if pbo_gate is not None
            else (
                None,
                [
                    "G4 thresholds were not applied to this PBO result -- these diagnostics "
                    "are reported, not gated on. An unevaluated gate is not a passed one."
                ],
            )
        )
        sections.insert(-2, _render_pbo_section(pbo_result, gate_ok, gate_reasons))
    lines = ["# Engine Validation & Trade-Simulation Report", ""]
    for section in sections:
        lines.extend(section)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
