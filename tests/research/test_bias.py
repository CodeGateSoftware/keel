"""Lookahead and recursive-bias detection (issue #440, C1a).

PBO/CSCV answers "were these parameters over-selected"; it says nothing about a
single-configuration strategy that reads information it could not have had. The harness
pinned here is the truncation diff: `Rule.detect(candles_by_tf)` is documented pure, and the
backtester's per-bar contract (backtest.py: `candles[: i + 1]`) defines "the decision at bar
N" as detect over data ending at N -- so a rule whose AT-BAR-N decision changes when future
bars are made visible reads the future.

Every fake rule below is built on real `Candle` series and states its anchor bar honestly
(`Setup.ts` is the bar the setup is about), which is what lets the harness attribute a
full-series setup to a prefix anchor and diff the two.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from keel.analysis.indicators import atr
from keel.research.bias import (
    LookaheadReport,
    RecursiveReport,
    lookahead_analysis,
    recursive_analysis,
    render_lines,
)
from keel.types import Candle, Granularity

HOUR = 3600
DAY = 86400


# -- synthetic series ------------------------------------------------------------------------------


def _peak_close_series(
    n: int = 120, *, peak: int = 60, spike_high_at: int = 110, start: int = 1_700_000_000
) -> list[Candle]:
    """Hourly candles whose max-CLOSE bar is `peak` (closes ascend to it, then descend) and
    whose max-HIGH bar is `spike_high_at` -- so a rule targeting "max high of everything I
    can see" is fed a different target by every prefix that stops before the spike."""
    candles = []
    for i in range(n):
        close = Decimal(100 + (i if i <= peak else 2 * peak - i))
        high = Decimal(500) if i == spike_high_at else close + Decimal(1)
        candles.append(
            Candle(
                ts=start + i * HOUR,
                open=close - Decimal(1),
                high=high,
                low=close - Decimal(2),
                close=close,
                volume=Decimal(10),
            )
        )
    return candles


def _two_tf_series(n_hours: int = 240, n_days: int = 15, *, start: int = 1_700_000_000):
    """Hourly bars over `n_days` distinct daily bars -- the engine's shape: a fine trading
    series plus a coarser higher-TF bias series."""
    daily = [
        Candle(
            ts=start + i * DAY,
            open=Decimal(100 + i),
            high=Decimal(101 + i),
            low=Decimal(99 + i),
            close=Decimal(100 + i),
            volume=Decimal(10),
        )
        for i in range(n_days)
    ]
    hourly = [
        Candle(
            ts=start + j * HOUR,
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100 + (j % 3)),
            volume=Decimal(1),
        )
        for j in range(n_hours)
    ]
    return {Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily}


def _setup(candles: list[Candle], *, ts: int, target: Decimal) -> Any:
    from keel.strategy.rules.base import Setup

    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=candles[0].close,
        stop=candles[0].close - Decimal(10),
        target=target,
        context={},
        ts=ts,
    )


# -- fake detectors --------------------------------------------------------------------------------


def _leaky_target_detect(candles_by_tf):
    """THE deliberately leaky rule: fires at the max-close bar and targets the max HIGH of
    the entire series it was handed -- a target no live engine could have known at the
    anchor bar."""
    from keel.strategy.rules.base import Setup

    candles = candles_by_tf[Granularity.ONE_HOUR]
    signal = max(candles, key=lambda c: c.close)
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=signal.close,
        stop=signal.close - Decimal(10),
        target=max(c.high for c in candles),
        context={},
        ts=signal.ts,
    )


def _dust_series(
    n: int = 120, *, peak: int = 60, spike_high_at: int = 40, start: int = 1_700_000_000
):
    """Peak-close shape with the series-wide HIGH SPIKE placed BEFORE the anchor bar, so the
    max high a prefix ending at the anchor sees EQUALS the full series' -- the only
    future-dependent term in the dust rules below is the dust itself."""
    return _peak_close_series(n, peak=peak, spike_high_at=spike_high_at, start=start)


def _dusty_target_detect(dust: Decimal):
    """Same scan shape, but the ONLY future-dependence is `dust` added once the series grows
    past the anchor -- used to pin the tolerance."""
    from keel.strategy.rules.base import Setup

    def detect(candles_by_tf):
        candles = candles_by_tf[Granularity.ONE_HOUR]
        signal = max(candles, key=lambda c: c.close)
        target = max(c.high for c in candles)
        if len(candles) > 61:
            target = target + dust
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=signal.close,
            stop=signal.close - Decimal(10),
            target=target,
            context={},
            ts=signal.ts,
        )

    return detect


def _future_created_signal_detect(candles_by_tf):
    """Fires at the max-close bar but only once five FUTURE bars exist past it -- the signal
    itself is manufactured by the future, not just its prices."""
    candles = candles_by_tf[Granularity.ONE_HOUR]
    best_i = max(range(len(candles)), key=lambda i: candles[i].close)
    if len(candles) <= best_i + 5:
        return None
    return _setup(candles, ts=candles[best_i].ts, target=candles[best_i].close + Decimal(20))


def _clean_last_bar_detect(candles_by_tf):
    """A clean rule: decides at its own last bar from bars <= that bar only (momentum), and
    ignores the higher timeframe entirely."""
    from keel.strategy.rules.base import Setup

    candles = candles_by_tf[Granularity.ONE_HOUR]
    if len(candles) < 2 or candles[-1].close <= candles[-2].close:
        return None
    close = candles[-1].close
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=close,
        stop=close - Decimal(10),
        target=close + Decimal(20),
        context={},
        ts=candles[-1].ts,
    )


def _closed_coarse_reader_detect(candles_by_tf):
    """A clean multi-timeframe rule: reads the last DAILY bar that is CLOSED by its anchor
    bar's close (ts + step <= anchor close), keyed on ts, so future daily bars are invisible
    to it by construction."""
    from keel.strategy.rules.base import Setup

    hourly = candles_by_tf[Granularity.ONE_HOUR]
    daily = candles_by_tf[Granularity.ONE_DAY]
    anchor_close_ts = hourly[-1].ts + HOUR
    closed = [c for c in daily if c.ts + DAY <= anchor_close_ts]
    if not closed or len(hourly) < 2 or hourly[-1].close <= hourly[-2].close:
        return None
    entry = closed[-1].close
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=entry,
        stop=entry - Decimal(10),
        target=entry + Decimal(20),
        context={},
        ts=hourly[-1].ts,
    )


def _blind_coarse_reader_detect(candles_by_tf):
    """THE engine-veto leak: reads the LAST daily bar blindly -- in live that bar is still
    forming at the anchor, and its closed-by-the-future version differs."""
    from keel.strategy.rules.base import Setup

    hourly = candles_by_tf[Granularity.ONE_HOUR]
    daily = candles_by_tf[Granularity.ONE_DAY]
    if len(hourly) < 2 or hourly[-1].close <= hourly[-2].close:
        return None
    entry = daily[-1].close
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=entry,
        stop=entry - Decimal(10),
        target=entry + Decimal(20),
        context={},
        ts=hourly[-1].ts,
    )


# -- lookahead_analysis -----------------------------------------------------------------------


def test_leaky_target_rule_is_detected_naming_the_anchor_bar() -> None:
    candles = _peak_close_series()
    report = lookahead_analysis(
        _leaky_target_detect, {Granularity.ONE_HOUR: candles}, rule_id="leaky"
    )
    assert isinstance(report, LookaheadReport)
    assert report.verdict == "lookahead_detected"
    assert report.n_divergences >= 1
    first = report.divergences[0]
    # The divergence names the bar that diverged (the max-close anchor, bar 60), the field,
    # and both values: the prefix saw only highs up to that bar, the full series saw the spike.
    assert first.bar_ts == candles[60].ts
    assert first.field == "target"
    assert Decimal(first.prefix_value) == Decimal(161)
    assert Decimal(first.full_value) == Decimal(500)


def test_clean_last_bar_rule_is_clean() -> None:
    candles_by_tf = _two_tf_series()
    report = lookahead_analysis(_clean_last_bar_detect, candles_by_tf, rule_id="clean", warmup=50)
    assert report.verdict == "clean"
    assert report.divergences == ()
    assert report.n_bars_checked == 240 - 50


def test_clean_closed_coarse_reader_is_clean_under_the_poison_view() -> None:
    """The higher-TF check must not flag the rule that does it RIGHT: one that keys its
    coarse reads on bars closed by the anchor sees the same data whether the harness hands
    it closed-at-anchor daily bars or the full daily series."""
    candles_by_tf = _two_tf_series()
    report = lookahead_analysis(
        _closed_coarse_reader_detect, candles_by_tf, rule_id="closed-coarse", warmup=50
    )
    assert report.verdict == "clean"
    assert report.divergences == ()


def test_blind_coarse_bar_reader_is_detected() -> None:
    """The engine-veto leak: a detect that reads the last coarse bar even when it is unclosed
    at the anchor diverges the moment the future closes that bar."""
    candles_by_tf = _two_tf_series()
    hourly = candles_by_tf[Granularity.ONE_HOUR]
    daily = candles_by_tf[Granularity.ONE_DAY]
    report = lookahead_analysis(
        _blind_coarse_reader_detect, candles_by_tf, rule_id="blind-coarse", warmup=50
    )
    assert report.verdict == "lookahead_detected"
    first = report.divergences[0]
    assert first.field == "entry"
    # The prefix (live) view reads the last daily bar CLOSED by anchor 50's close; the
    # future-extended view reads the last daily bar of the whole series -- different bars.
    assert first.bar_ts == hourly[50].ts
    assert Decimal(first.prefix_value) != Decimal(first.full_value)
    assert Decimal(first.full_value) == daily[-1].close


def test_decimal_dust_below_tolerance_is_clean() -> None:
    candles = _dust_series()
    report = lookahead_analysis(
        _dusty_target_detect(Decimal("0.000000001")),
        {Granularity.ONE_HOUR: candles},
        rule_id="dust",
    )
    assert report.verdict == "clean"
    assert report.divergences == ()


def test_dust_above_tolerance_is_detected() -> None:
    candles = _dust_series()
    report = lookahead_analysis(
        _dusty_target_detect(Decimal("0.001")), {Granularity.ONE_HOUR: candles}, rule_id="dust"
    )
    assert report.verdict == "lookahead_detected"
    assert report.divergences[0].field == "target"


def test_future_created_signal_reports_setup_present_divergence() -> None:
    candles = _peak_close_series()
    report = lookahead_analysis(
        _future_created_signal_detect, {Granularity.ONE_HOUR: candles}, rule_id="ghost"
    )
    assert report.verdict == "lookahead_detected"
    first = report.divergences[0]
    assert first.field == "setup_present"
    assert first.bar_ts == candles[60].ts
    assert first.prefix_value == "absent"
    assert first.full_value == "present"


def test_sample_step_bounds_cost_and_detection_still_lands() -> None:
    candles = _peak_close_series()
    report = lookahead_analysis(
        _leaky_target_detect,
        {Granularity.ONE_HOUR: candles},
        rule_id="leaky",
        sample_step=10,
    )
    # 50, 60, ..., 110 walked by the stride, plus the final bar always included.
    assert report.sample_step == 10
    assert report.n_bars_checked == 8
    assert report.verdict == "lookahead_detected"
    assert report.divergences[0].bar_ts == candles[60].ts


def test_only_first_k_divergences_are_kept_but_all_are_counted() -> None:
    candles_by_tf = _two_tf_series()
    report = lookahead_analysis(
        _blind_coarse_reader_detect,
        candles_by_tf,
        rule_id="blind",
        warmup=50,
        sample_step=25,
    )
    assert report.verdict == "lookahead_detected"
    assert len(report.divergences) == 5
    assert report.n_divergences > 5
    # Every reported divergence names a walked anchor bar.
    anchors = {
        candles_by_tf[Granularity.ONE_HOUR][i].ts
        for i in (50, 75, 100, 125, 150, 175, 200, 225, 239)
    }
    for divergence in report.divergences:
        assert divergence.bar_ts in anchors


def test_short_series_is_clean_without_crashing() -> None:
    candles = _peak_close_series(n=30)
    report = lookahead_analysis(
        _leaky_target_detect, {Granularity.ONE_HOUR: candles}, rule_id="short"
    )
    assert report.verdict == "clean"
    assert report.n_bars_checked == 0
    assert report.divergences == ()


def test_rule_that_never_fires_is_clean() -> None:
    candles = _peak_close_series()
    report = lookahead_analysis(
        lambda candles_by_tf: None, {Granularity.ONE_HOUR: candles}, rule_id="silent"
    )
    assert report.verdict == "clean"
    assert report.n_bars_checked == 70


# -- coverage notes -------------------------------------------------------------------------------


def test_single_tf_report_notes_the_higher_tf_axis_did_not_run() -> None:
    """No coarser series in the dataset -> Axis B never ran -> the report says so, and the
    render prints it: a clean single-TF verdict must not read as full coverage."""
    candles = _peak_close_series()
    report = lookahead_analysis(
        _leaky_target_detect, {Granularity.ONE_HOUR: candles}, rule_id="solo"
    )
    assert report.notes == ("higher-TF axis not run: no coarser series cached",)
    joined = "\n".join(render_lines(report))
    assert "higher-TF axis not run: no coarser series cached" in joined


def test_multi_tf_report_carries_no_notes() -> None:
    candles_by_tf = _two_tf_series()
    report = lookahead_analysis(
        _closed_coarse_reader_detect, candles_by_tf, rule_id="two-tf", warmup=50
    )
    assert report.notes == ()


# -- recursive_analysis -----------------------------------------------------------------------


def _constant_range_candles(n: int = 200) -> list[Candle]:
    """Every bar has the identical true range (open = prior close), so Wilder ATR is exactly
    that range from the first seeded sample onward -- the converging case."""
    return [
        Candle(
            ts=1_700_000_000 + i * HOUR,
            open=Decimal(100),
            high=Decimal(110),
            low=Decimal(100),
            close=Decimal(110),
            volume=Decimal(1),
        )
        for i in range(n)
    ]


def test_converging_indicator_is_stable() -> None:
    candles = _constant_range_candles()
    report = recursive_analysis(
        candles,
        indicator_fn=lambda cs: atr(cs, period=14)[-1],
        min_warmup=56,
        rule_id="turtle",
        indicator_name="atr(14)[-1]",
    )
    assert isinstance(report, RecursiveReport)
    assert report.verdict == "stable"
    assert report.max_drift <= 1e-8


def test_drifting_indicator_is_flagged_naming_worst_n() -> None:
    candles = _constant_range_candles()
    report = recursive_analysis(
        candles,
        indicator_fn=lambda cs: float(len(cs)),  # never converges: value IS the history length
        min_warmup=50,
        rule_id="drifter",
        indicator_name="len(candles)",
    )
    assert report.verdict == "recursive_drift"
    assert report.max_drift == 25.0  # |v(N) - v(N+step)| with step=25
    assert report.worst_n is not None and report.worst_n == 50


# -- render_lines ------------------------------------------------------------------------------


def test_render_lines_names_rule_verdict_and_first_divergences() -> None:
    candles = _peak_close_series()
    report = lookahead_analysis(
        _leaky_target_detect, {Granularity.ONE_HOUR: candles}, rule_id="rule-7"
    )
    lines = render_lines(report)
    joined = "\n".join(lines)
    assert "rule-7" in joined
    assert "lookahead_detected" in joined
    assert f"ts={candles[60].ts}" in joined
    assert "field=target" in joined
    assert "161" in joined and "500" in joined


def test_render_lines_recursive_names_worst_drift() -> None:
    candles = _constant_range_candles()
    report = recursive_analysis(
        candles,
        indicator_fn=lambda cs: float(len(cs)),
        min_warmup=50,
        rule_id="rule-9",
        indicator_name="len(candles)",
    )
    lines = render_lines(report)
    joined = "\n".join(lines)
    assert "rule-9" in joined
    assert "recursive_drift" in joined
    assert "N=50" in joined
    assert "25.0" in joined or "2.5" in joined  # the drift, in whatever notation rendered
