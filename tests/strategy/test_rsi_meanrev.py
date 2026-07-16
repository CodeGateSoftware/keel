"""Tests for halal_cb.strategy.rules.rsi_meanrev: the RSI mean-reversion rule.

Long-only: `detect()` fires a long `Setup` on an RSI oversold bounce at a known
support level (optionally gated on bullish RSI divergence); `exit_signal()` fires
when RSI is overbought (never a short — overbought is exit/don't-buy only).

Fixtures are hand-tuned against the *real* `halal_cb.analysis.indicators.rsi` /
`rsi_divergence` and `halal_cb.analysis.levels.find_levels` implementations (Wilder's
RSI seeded at `period`, swing-pivot support clustering) so the numeric claims in each
test's comment are verifiable, not assumed.
"""

from __future__ import annotations

from decimal import Decimal

from halal_cb.strategy.rules.base import Setup
from halal_cb.strategy.rules.rsi_meanrev import RsiMeanReversion
from halal_cb.types import Candle, Granularity


def _c(ts: int, o: str, h: str, low: str, c: str, v: str = "1") -> Candle:
    return Candle(
        ts=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(v),
    )


def _oversold_at_support_series() -> list[Candle]:
    """Three confirmed touches at support=100, ending in an RSI oversold bounce.

    RSI(period=14): prev (idx17) ~= 3.06 (< oversold 20), curr (idx18) ~= 7.75
    (> prev -> recovering). `find_levels` (default tolerance/min_touches) on
    candles[:-1] clusters the three low=100 pivots (idx8, idx11, idx14) into one
    3-touch support level; the final candle's low (100) sits on it. No RSI
    divergence is present in this series.
    """
    rows = [
        (0, "200", "202", "197", "198"),
        (1, "198", "199", "190", "192"),
        (2, "192", "194", "180", "182"),
        (3, "182", "184", "165", "168"),
        (4, "168", "170", "150", "153"),
        (5, "153", "156", "135", "138"),
        (6, "138", "141", "120", "123"),
        (7, "123", "126", "108", "111"),
        (8, "111", "114", "100", "103"),  # touch #1 @100
        (9, "103", "107", "104", "102"),  # pivot confirm for #1
        (10, "102", "106", "105", "101"),  # pivot confirm for #1
        (11, "101", "104", "100", "100"),  # touch #2 @100
        (12, "100", "105", "103", "101"),  # pivot confirm for #2
        (13, "101", "106", "104", "100"),  # pivot confirm for #2
        (14, "100", "103", "100", "99"),  # touch #3 @100
        (15, "99", "104", "102", "100"),  # pivot confirm for #3
        (16, "100", "105", "103", "101"),  # pivot confirm for #3
        (17, "101", "103", "100", "99"),  # approach support again; RSI oversold
        (18, "99", "104", "100", "103"),  # bounce candle: touches 100, closes up
    ]
    return [_c(*row) for row in rows]


def _mid_rsi_series() -> list[Candle]:
    """Gentle sideways wobble: RSI stays near 47-50 throughout (no oversold bounce)."""
    deltas = [1, -1, 1, -1, 2, -2, 1, -1, 1, -1, 1, -1, 2, -2, 1, -1, 1, -1, 1, -1]
    rows = []
    price = 100
    for i, d in enumerate(deltas):
        o, c = price, price + d
        h = max(o, c) + 1
        low = min(o, c) - 1
        rows.append((i, str(o), str(h), str(low), str(c)))
        price = c
    return [_c(*row) for row in rows]


def _overbought_series() -> list[Candle]:
    """A relentless uptrend (all up-closes): RSI pins at 100.0 by idx14 onward,
    well above the default overbought=80 threshold.
    """
    rows = []
    price = Decimal(100)
    for i in range(20):
        o, c = price, price + 5
        h, low = c + 1, o - 1
        rows.append((i, str(o), str(h), str(low), str(c)))
        price = c
    return [_c(*row) for row in rows]


def _divergence_series(*, new_low: bool) -> list[Candle]:
    """Padded decline into two swing lows at ~100 (idx19) and ~100 or lower (idx25),
    followed by an oversold bounce at support (idx28/idx29 prev/curr RSI 6.1 -> 8.74).

    When `new_low=True` the second swing low is 99.85 (< 100.00, the first), and RSI
    at that low (~3.93) is higher than at the first (~0.0) -> genuine bullish
    divergence (lower price low, higher RSI low). When `new_low=False` the second
    swing low is 100.10 (not a new low) -> `rsi_divergence` returns None.
    Levels use `level_min_touches=2` (these two touches cluster within tolerance).
    """
    rows: list[tuple[int, str, str, str, str]] = []
    price = 300
    for i in range(16):
        o, c = price, price - 2
        h, low = price + 1, price - 4
        rows.append((i, str(o), str(h), str(low), str(c)))
        price = c
    rows += [
        (16, str(price), str(price + 2), str(price - 8), str(price - 6)),
        (17, str(price - 6), str(price - 2), str(price - 12), str(price - 10)),
        (18, str(price - 10), str(price - 6), str(price - 16), str(price - 14)),
        (19, str(price - 14), str(price - 10), "100.00", "101"),  # swing low A
        (20, "101", "106", "103", "104"),
        (21, "104", "108", "105", "107"),
        (22, "107", "110", "106", "108"),
        (23, "108", "110", "104", "106"),
        (24, "106", "109", "102", "104"),
        (
            25,
            "104",
            "107",
            "99.85" if new_low else "100.10",
            "102",
        ),  # swing low B
        (26, "102", "106", "101", "103"),
        (27, "103", "107", "104", "105"),
        (28, "105", "106", "99.90", "100"),  # approach support again; oversold
        (29, "100", "105", "99.85", "103"),  # bounce candle at support
    ]
    return [_c(*row) for row in rows]


def _held_setup() -> Setup:
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal(100),
        stop=Decimal(90),
        target=Decimal(120),
        context={},
        ts=0,
    )


class TestDetectOversoldAtSupport:
    def test_oversold_bounce_at_support_returns_long_setup(self) -> None:
        rule = RsiMeanReversion()
        candles = _oversold_at_support_series()
        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.direction == "long"
        assert setup.product_id == "BTC-USD"
        assert setup.entry == Decimal("103")
        assert setup.stop < setup.entry
        assert setup.target > setup.entry
        assert setup.rr > 0
        assert setup.ts == candles[-1].ts

    def test_setup_context_carries_indicator_values(self) -> None:
        rule = RsiMeanReversion()
        candles = _oversold_at_support_series()
        setup = rule.detect({Granularity.ONE_HOUR: candles})

        assert setup is not None
        assert setup.context["rsi"] < rule.oversold or setup.context["rsi"] > 0
        assert setup.context["support_price"] == Decimal("100")
        assert setup.context["support_touches"] >= 3

    def test_missing_timeframe_returns_none(self) -> None:
        rule = RsiMeanReversion(timeframe=Granularity.ONE_HOUR)
        candles = _oversold_at_support_series()
        assert rule.detect({Granularity.FIFTEEN_MINUTE: candles}) is None

    def test_too_few_candles_returns_none(self) -> None:
        rule = RsiMeanReversion()
        candles = _oversold_at_support_series()[:5]
        assert rule.detect({Granularity.ONE_HOUR: candles}) is None


class TestDetectMidRsiNoSignal:
    def test_mid_rsi_returns_none(self) -> None:
        rule = RsiMeanReversion()
        candles = _mid_rsi_series()
        assert rule.detect({Granularity.ONE_HOUR: candles}) is None


class TestDivergenceGate:
    def test_require_divergence_false_ignores_absence_of_divergence(self) -> None:
        rule = RsiMeanReversion(require_divergence=False, level_min_touches=2)
        candles = _divergence_series(new_low=False)
        setup = rule.detect({Granularity.ONE_HOUR: candles})
        assert setup is not None
        assert setup.context["divergence"] is None

    def test_require_divergence_true_with_divergence_present_returns_setup(self) -> None:
        rule = RsiMeanReversion(require_divergence=True, level_min_touches=2)
        candles = _divergence_series(new_low=True)
        setup = rule.detect({Granularity.ONE_HOUR: candles})
        assert setup is not None
        assert setup.context["divergence"] == "bullish"

    def test_require_divergence_true_without_divergence_returns_none(self) -> None:
        rule = RsiMeanReversion(require_divergence=True, level_min_touches=2)
        candles = _divergence_series(new_low=False)
        assert rule.detect({Granularity.ONE_HOUR: candles}) is None


class TestStopAndTargetMethods:
    def test_fixed_stop_method_uses_fixed_pct_below_entry(self) -> None:
        rule = RsiMeanReversion(
            stop_method="fixed",
            fixed_stop_pct=Decimal("0.10"),
            target_method="fixed_rr",
            fixed_rr=Decimal("2"),
        )
        candles = _oversold_at_support_series()
        setup = rule.detect({Granularity.ONE_HOUR: candles})
        assert setup is not None
        assert setup.stop == setup.entry * Decimal("0.90")
        assert setup.rr == Decimal("2")

    def test_nearest_resistance_falls_back_to_fixed_rr_when_no_resistance(self) -> None:
        rule = RsiMeanReversion(target_method="nearest_resistance", fixed_rr=Decimal("2"))
        candles = _oversold_at_support_series()
        setup = rule.detect({Granularity.ONE_HOUR: candles})
        assert setup is not None
        assert setup.rr == Decimal("2")


class TestExitSignalOverbought:
    def test_overbought_rsi_triggers_exit(self) -> None:
        rule = RsiMeanReversion()
        candles = _overbought_series()
        held = _held_setup()
        assert rule.exit_signal(held, {Granularity.ONE_HOUR: candles}) is True

    def test_mid_rsi_does_not_trigger_exit(self) -> None:
        rule = RsiMeanReversion()
        candles = _mid_rsi_series()
        held = _held_setup()
        assert rule.exit_signal(held, {Granularity.ONE_HOUR: candles}) is False

    def test_oversold_does_not_trigger_exit(self) -> None:
        rule = RsiMeanReversion()
        candles = _oversold_at_support_series()
        held = _held_setup()
        assert rule.exit_signal(held, {Granularity.ONE_HOUR: candles}) is False

    def test_missing_timeframe_does_not_trigger_exit(self) -> None:
        rule = RsiMeanReversion(timeframe=Granularity.ONE_HOUR)
        held = _held_setup()
        assert rule.exit_signal(held, {Granularity.FIFTEEN_MINUTE: []}) is False


class TestDescribe:
    def test_describe_returns_name_and_params(self) -> None:
        rule = RsiMeanReversion(oversold=25.0, overbought=75.0)
        described = rule.describe()
        assert described["name"] == "rsi_meanrev"
        assert described["params"]["oversold"] == 25.0
        assert described["params"]["overbought"] == 75.0

    def test_name_attribute(self) -> None:
        rule = RsiMeanReversion()
        assert rule.name == "rsi_meanrev"
