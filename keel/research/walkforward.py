"""Rolling-origin walk-forward validation of ONE parameter set (#445).

A walk-forward split answers a narrow question: GIVEN this parameter set -- fixed before any
fold is run, never chosen here -- does it hold up out-of-sample across a rolling series of
train/test windows, and does its out-of-sample performance degrade as the data moves away
from the period the set was conceived on? It measures stability. It does not measure, score
or compare alternatives, and it must never be handed the second job.

⛔ STRATHERN RAIL (spec §6, the same rail `keel/research/cscv.py` states). A walk-forward
validator that reported a "winning window" -- the fold, window size or step whose test
metrics look strongest -- would reintroduce exactly the selection-over-configurations the
rail exists to forbid: the PBO machinery says a score may report and may gate, but may never
be a ranking key. So this module validates a GIVEN rule across GIVEN folds and reports
per-fold test metrics plus aggregate stability; no public function returns a fold, window
or parameter set to favour, because none is ever computed. `tests/research/test_walkforward.py`
enforces this two ways: a source scan for ranking shapes, and a dataclass-field scan for
selection-bearing fields.

Determinism is total: no sampling, no RNG, no seed. Two runs over the same inputs are the
same report (pinned by test).

Decimal discipline matches the rest of `keel/research`: per-fold metrics are exact Decimals
(they derive from money), while the deflate-derived window-size guidance at render time is
float, exactly as `deflate.py` documents its probabilities -- none of it is money.

Fold semantics (rolling-origin, sliding both windows):
    fold k:  train [k*step, k*step + train_bars)  test [k*step + train_bars, ... + test_bars)
`step` defaults to `test_bars` (non-overlapping tests); a smaller step overlaps them.

Warmup/context, stated rather than implied. Rules need lookback before they can detect, so a
slice's first bars are spent warming up. The TRAIN run of fold k is walked over
`candles[:train_end]` -- the window PLUS every earlier bar, when any exist, so its indicators
enter the window with full context -- and its metrics count only trades ENTERED inside the
train window. Fold 0 has no earlier bars, so its train run warms up inside its own window;
that asymmetry is the honest reading of "when available" and biases nothing downstream (the
train side exists for the in-sample vs out-of-sample honesty table, never for selection).
The TEST run is walked over the test bars ALONE: the out-of-sample side is given no
information from before its window, so it pays its own warmup cost -- fewer possible test
trades, i.e. the conservative direction, never the flattering one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from keel.research import deflate as deflate_mod
from keel.strategy import backtest as backtest_mod
from keel.strategy.rules.base import Rule
from keel.strategy.stats import summarize
from keel.types import Candle

__all__ = [
    "FoldBounds",
    "FoldMetrics",
    "WalkForwardReport",
    "folds",
    "render_lines",
    "walk_forward",
]

#: 365.25 days, the Julian year candle timestamps are counted against. An assumption like
#: any other time-normalisation; stated here so a reader can disagree with it in one place.
SECONDS_PER_YEAR = Decimal("31557600")

#: The statement every render carries, whatever the data did. It is printed once per
#: report precisely because a walk-forward table LOOKS like a scoreboard; the statement is
#: what says it is not one.
_NOT_A_RANKING_NOTE = (
    "validates ONE parameter set across folds and reports stability -- "
    "it does not rank folds, windows or parameter sets"
)


@dataclass(frozen=True)
class FoldBounds:
    """Half-open bar indices of one fold: train [train_start, train_end), test
    [test_start, test_end), with train_end == test_start (windows adjacent, disjoint)."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class FoldMetrics:
    """What one fold actually measured, bounds included so a report is self-describing.

    The TEST side is the finding; `train_*` exists so the report can state in-sample vs
    out-of-sample side by side (the honesty table). `test_trade_pnl` is the fold's closed
    per-trade test P&L in run order -- the series the CLI's ledger row for this fold
    carries, so a reader can recompute every number above from the ledger alone."""

    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_n_trades: int
    train_expectancy: Decimal
    test_n_trades: int
    test_expectancy: Decimal
    test_win_rate: Decimal
    test_max_drawdown: Decimal
    test_trade_pnl: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class WalkForwardReport:
    """Per-fold test metrics plus aggregates for ONE given rule. Carries the identity of
    nothing to favour -- see the module docstring's Strathern rail.

    Halves convention (pinned by test): with an ODD fold count the middle fold belongs to
    neither half -- the early half is the first n//2 folds, the late half the last n//2 --
    so the degradation compares equal-sized halves around a dropped middle. With fewer than
    two folds there are no halves and `degradation` is None rather than an invented 0.

    `test_trade_pnl` pools every fold's closed test P&L (run order) and `test_span_seconds`
    spans the first test window's first bar to the last test window's last bar; the two
    exist only to feed the render-time window-size guidance its raw quantities."""

    rule_name: str
    rule_params: dict[str, Any]
    n_folds: int
    fold_metrics: tuple[FoldMetrics, ...]
    median_test_expectancy: Decimal
    n_folds_test_positive: int
    early_half_median: Decimal | None
    late_half_median: Decimal | None
    degradation: Decimal | None
    stability_note: str
    test_trade_pnl: tuple[Decimal, ...] = ()
    test_span_seconds: int = 0


def folds(
    n_bars: int, *, train_bars: int, test_bars: int, step_bars: int | None = None
) -> list[FoldBounds]:
    """Rolling-origin fold bounds over `n_bars` bars: fold 0 trains on [0, train_bars) and
    tests on the next `test_bars`; each further fold advances BOTH windows by `step_bars`
    (default `test_bars`, i.e. non-overlapping test windows; a smaller step overlaps them).
    The last fold never exceeds `n_bars` -- a trailing remainder too small for a full
    train+test window yields no fold, never a short one.

    Raises `ValueError`, naming the offending window against the available bars, for a
    non-positive `n_bars`/`train_bars`/`test_bars`/`step_bars` or a train+test window that
    cannot fit even once."""
    if n_bars <= 0:
        raise ValueError(f"n_bars must be positive, got {n_bars}")
    if train_bars <= 0:
        raise ValueError(f"train_bars must be positive, got {train_bars}")
    if test_bars <= 0:
        raise ValueError(f"test_bars must be positive, got {test_bars}")
    step = test_bars if step_bars is None else step_bars
    if step <= 0:
        raise ValueError(f"step_bars must be positive, got {step_bars}")
    if train_bars + test_bars > n_bars:
        raise ValueError(
            f"train_bars ({train_bars}) + test_bars ({test_bars}) = "
            f"{train_bars + test_bars} exceeds the {n_bars} available bars -- no fold fits; "
            "lengthen the candle series or shrink the windows"
        )
    out: list[FoldBounds] = []
    start = 0
    while start + train_bars + test_bars <= n_bars:
        out.append(
            FoldBounds(
                train_start=start,
                train_end=start + train_bars,
                test_start=start + train_bars,
                test_end=start + train_bars + test_bars,
            )
        )
        start += step
    return out


def _closed_pnl(trade: Any) -> Decimal:
    """A closed trade's realised P&L; an open trade has none and is excluded by callers
    (the same invariant `strategy.stats.summarize` states)."""
    if trade.pnl is None:
        raise ValueError(
            f"a {trade.outcome!r} trade reached the aggregate without a pnl -- only an "
            "open trade may omit realised P&L"
        )
    return trade.pnl


def _median(values: Sequence[Decimal]) -> Decimal:
    """Exact-Decimal median. `sorted` with NO key: ordering values to find a middle is an
    aggregate, not a selection -- the distinction the module's Strathern rail turns on."""
    ordered = sorted(values)
    n = len(ordered)
    middle = n // 2
    if n % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _stability_note(n_folds: int, n_positive: int, degradation: Decimal | None) -> str:
    """A description of what the folds showed, and nothing else -- no fold, window or
    parameter set is pointed at, because none was compared for selection."""
    parts = [f"positive in {n_positive}/{n_folds} folds on test expectancy"]
    if degradation is None:
        parts.append("degradation not computable (fewer than two halves to compare)")
    elif degradation < 0:
        parts.append("late-half median test expectancy below the early half")
    elif degradation > 0:
        parts.append("late-half median test expectancy above the early half")
    else:
        parts.append("late-half median test expectancy level with the early half")
    return "; ".join(parts)


def walk_forward(
    rule: Rule,
    candles: list[Candle],
    *,
    folds_bounds: Sequence[FoldBounds],
    fee_pct: Decimal = backtest_mod.TAKER_FEE_PCT,
    slippage_pct: Decimal = backtest_mod.SLIPPAGE_FLOOR_PCT,
) -> WalkForwardReport:
    """Backtest `rule` over every fold in `folds_bounds` (see `folds` for the geometry and
    the module docstring for the warmup contract) and aggregate the TEST-side metrics.

    `fee_pct`/`slippage_pct` default to the engine's own and should be reported beside any
    number this returns -- the same rule `backtest` itself states. Deterministic: same
    inputs, same report, always."""
    if not folds_bounds:
        raise ValueError("folds_bounds is empty -- no fold to validate (see `folds`)")

    metrics: list[FoldMetrics] = []
    pooled: list[Decimal] = []
    span_first_ts: int | None = None
    span_last_ts: int | None = None

    for index, bounds in enumerate(folds_bounds):
        # TRAIN: walk the window plus every earlier bar (indicator context "when
        # available"), then count only trades ENTERED inside the window itself.
        train_run = backtest_mod.backtest(
            rule,
            candles[: bounds.train_end],
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        train_start_ts = candles[bounds.train_start].ts
        train_end_ts = candles[bounds.train_end - 1].ts
        window_trades = [
            t
            for t in train_run.trades
            if t.outcome != "open" and train_start_ts <= t.entry_ts <= train_end_ts
        ]
        train_stats = summarize(window_trades)

        # TEST: the window's bars alone -- no earlier-bar context on the out-of-sample
        # side, so it pays its own warmup (the conservative direction).
        test_run = backtest_mod.backtest(
            rule,
            candles[bounds.test_start : bounds.test_end],
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
        )
        test_trades = [t for t in test_run.trades if t.outcome != "open"]
        test_pnls = tuple(_closed_pnl(t) for t in test_trades)
        test_stats = summarize(test_run.trades)
        wins = sum(1 for t in test_trades if t.outcome == "win")
        n_test = len(test_trades)

        metrics.append(
            FoldMetrics(
                fold_index=index,
                train_start=bounds.train_start,
                train_end=bounds.train_end,
                test_start=bounds.test_start,
                test_end=bounds.test_end,
                train_n_trades=len(window_trades),
                train_expectancy=train_stats.expectancy,
                test_n_trades=n_test,
                test_expectancy=test_stats.expectancy,
                test_win_rate=(Decimal(wins) / Decimal(n_test)) if n_test else Decimal(0),
                test_max_drawdown=test_stats.max_drawdown,
                test_trade_pnl=test_pnls,
            )
        )
        pooled.extend(test_pnls)
        fold_first_ts = candles[bounds.test_start].ts
        fold_last_ts = candles[bounds.test_end - 1].ts
        if span_first_ts is None or fold_first_ts < span_first_ts:
            span_first_ts = fold_first_ts
        if span_last_ts is None or fold_last_ts > span_last_ts:
            span_last_ts = fold_last_ts

    expectancies = [m.test_expectancy for m in metrics]
    half = len(metrics) // 2
    early_median: Decimal | None = None
    late_median: Decimal | None = None
    degradation: Decimal | None = None
    if half:
        early_median = _median(expectancies[:half])
        late_median = _median(expectancies[len(metrics) - half :])
        degradation = late_median - early_median
    n_positive = sum(1 for value in expectancies if value > 0)

    return WalkForwardReport(
        rule_name=getattr(rule, "name", type(rule).__name__),
        rule_params=dict(getattr(rule, "params", {}) or {}),
        n_folds=len(metrics),
        fold_metrics=tuple(metrics),
        median_test_expectancy=_median(expectancies),
        n_folds_test_positive=n_positive,
        early_half_median=early_median,
        late_half_median=late_median,
        degradation=degradation,
        stability_note=_stability_note(len(metrics), n_positive, degradation),
        test_trade_pnl=tuple(pooled),
        test_span_seconds=(
            (span_last_ts - span_first_ts)
            if span_first_ts is not None and span_last_ts is not None
            else 0
        ),
    )


def _guidance_lines(report: WalkForwardReport) -> list[str]:
    """Render-time window-size notes from `deflate` -- REPORT-ONLY, never a gate and never
    a selection. The question they answer is "is this window long enough for the observed
    performance to mean anything at all", and anything the report cannot compute is stated
    as NOT COMPUTABLE rather than filled in with a plausible default (the same honesty rule
    `trials deflate` follows).

    `n_trials` for the deflation is the fold count: each fold's test window is one look at
    the same parameter set, so `E[max]` inflates the luck of the luckiest window -- the
    conservative reading of how many times this run looked. Floats throughout, per
    `deflate.py`'s own unit note: Sharpe ratios and years are not money."""

    def refused(why: str) -> list[str]:
        return [header, f"  NOT COMPUTABLE: {why}"]

    header = (
        "window-size guidance -- is this window long enough to mean anything? "
        "(report-only, never a gate)"
    )
    if report.n_folds < 2:
        return refused("a single fold leaves deflation nothing to deflate")
    n = len(report.test_trade_pnl)
    if n < 2:
        return refused("fewer than two closed test trades across all folds")
    mean = sum(report.test_trade_pnl, Decimal(0)) / n
    variance = sum(((x - mean) ** 2 for x in report.test_trade_pnl), Decimal(0)) / n
    std = variance.sqrt()
    if std == 0:
        return refused("zero dispersion across test trades (every trade identical)")
    if report.test_span_seconds <= 0:
        return refused("test span is empty -- cannot express trades per year")
    years = Decimal(report.test_span_seconds) / SECONDS_PER_YEAR
    trades_per_year = n / float(years)
    if trades_per_year <= 0:
        return refused("non-positive trades per year")
    annual_sharpe = float(mean / std) * math.sqrt(trades_per_year)

    if annual_sharpe <= 0:
        return [
            header,
            f"  observed annualised test Sharpe : {annual_sharpe:.3f}",
            "  MinBTL is unbounded: no window length makes a non-positive Sharpe "
            "distinguishable from luck, so there is nothing to size a window against",
        ]

    n_looks = report.n_folds
    min_trades = deflate_mod.min_trades(n_looks, annual_sharpe, trades_per_year)
    min_years = deflate_mod.min_backtest_length_years(n_looks, annual_sharpe)
    return [
        header,
        f"  folds evaluated (N looks)       : {n_looks}",
        f"  observed annualised test Sharpe : {annual_sharpe:.3f}",
        f"  test trades/year                : {trades_per_year:.1f}",
        f"  min trades at that Sharpe (73)  : {min_trades:.0f}  (closed test trades observed: {n})",
        f"  MinBTL (years)                  : {min_years:.2f}  (test span: {float(years):.2f} yr)",
        "  stopping-rule guidance about window SIZE, judged by the reader -- "
        "it selects nothing and gates nothing",
    ]


def render_lines(report: WalkForwardReport) -> list[str]:
    """The report as lines: one row per fold, the aggregates, and the deflate guidance.
    Every render carries the not-a-ranking statement -- see `_NOT_A_RANKING_NOTE`."""
    lines = [
        f"walk-forward: {report.rule_name} over {report.n_folds} rolling-origin fold(s)",
        f"  {_NOT_A_RANKING_NOTE}",
        "",
        "  fold   train window   test window    trades tr/te   train exp   test exp   "
        "test win   test maxDD",
    ]
    for m in report.fold_metrics:
        lines.append(
            f"  {m.fold_index:>4}   "
            f"[{m.train_start:>4},{m.train_end:>4})   "
            f"[{m.test_start:>4},{m.test_end:>4})   "
            f"{m.train_n_trades:>6}/{m.test_n_trades:<6} "
            f"{m.train_expectancy:>10} {m.test_expectancy:>10} "
            f"{m.test_win_rate:>9} {m.test_max_drawdown:>11}"
        )
    halves = "middle fold excluded from both halves" if report.n_folds % 2 else "equal halves"
    lines += [
        "",
        f"  median test expectancy : {report.median_test_expectancy}",
        f"  folds test-positive    : {report.n_folds_test_positive}/{report.n_folds}",
        f"  degradation            : {report.degradation} "
        f"(late-half median minus early-half; {halves})",
        f"  stability note         : {report.stability_note}",
        "",
    ]
    lines += _guidance_lines(report)
    return lines
