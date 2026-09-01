"""#341 -- CUSUM event-driven entry gating.

The rule trades on EVENTS rather than on a clock: it enters only when price has cumulatively
moved past a threshold since the last event, and the threshold is stated as a multiple of one
round trip rather than as a percentage.

Two properties carry the design and everything here exists to hold them:

* **The filter resets when it fires.** Without that it is a trend detector with extra steps —
  `S+` climbs monotonically through a rally and stays above the threshold for every later bar,
  so the rule fires on every bar of the move. That is the every-bar evaluation this rule exists
  to replace.
* **The threshold is friction-scaled.** The source's 2.0–2.5% is not a conservative setting on
  this venue, it is almost exactly one round trip (2 × 1.2% taker + 2 × 0.05% slippage). A knob
  spelled as a percentage hides that; a knob spelled as a multiple cannot.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.base import Setup
from keel.strategy.rules.cusum_event import (
    ROUND_TRIP_FRICTION_PCT,
    CusumEvent,
    cusum_read,
)
from keel.types import Candle, Granularity
from tests.strategy.rule_conformance import RuleConformanceTests

_HOUR = 3600


def _candles(closes: list[float], *, start_ts: int = 1_700_000_000) -> list[Candle]:
    """A series whose closes are exactly `closes`, with a small symmetric range each bar so
    ATR is positive (a zero ATR declines before the filter is ever consulted)."""
    out: list[Candle] = []
    for index, close in enumerate(closes):
        price = Decimal(str(close))
        out.append(
            Candle(
                ts=start_ts + index * _HOUR,
                open=price,
                high=price * Decimal("1.004"),
                low=price * Decimal("0.996"),
                close=price,
                volume=Decimal("100"),
            )
        )
    return out


def _flat_then(rise_pct: float, *, bars: int, flat: int = 200) -> list[Candle]:
    """`flat` bars at 100, then `bars` bars moving by `rise_pct` each.

    ⚠️ **The move must CROSS ON THE FINAL BAR, and building these fixtures is what makes that
    concrete.** A rally that crossed earlier in the window has already fired and re-anchored,
    so the last bar reads a small `S+` and correctly declines. A fixture with "comfortably
    more" rise than the threshold therefore does NOT fire — which is how the first draft of
    this file failed, and is exactly the reset property under test.
    """
    closes = [100.0] * flat
    price = 100.0
    for _ in range(bars):
        price *= 1 + rise_pct / 100
        closes.append(price)
    return _candles(closes)


def _firing() -> dict[Granularity, list[Candle]]:
    """Crosses the default 5% threshold (2 × the 2.5% round trip) on the last bar: four bars
    of 1.2% sum to 4.8% and decline, the fifth reaches 6.0% and fires."""
    return {Granularity.ONE_HOUR: _flat_then(1.2, bars=5)}


# -- the shared contract ------------------------------------------------------------------------


class TestCusumEventConformance(RuleConformanceTests):
    def rule(self) -> CusumEvent:
        return CusumEvent(product_id="BTC-USD")

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        return _firing()


# -- the filter itself --------------------------------------------------------------------------


def test_the_threshold_is_a_multiple_of_one_round_trip() -> None:
    """The design ask of #341, asserted against the constants the backtest actually charges."""
    assert ROUND_TRIP_FRICTION_PCT == Decimal("0.025")
    rule = CusumEvent(product_id="BTC-USD", threshold_friction_mult=Decimal("2"))
    assert rule.threshold_pct == Decimal("0.05")


def test_the_sources_own_setting_is_exactly_break_even_here() -> None:
    """The finding that motivated expressing the knob this way.

    A 2.5% event threshold sounds conservative and is not: it is one round trip on this venue,
    so the paper's setting names a move that pays for the trade and leaves nothing. Spelled as
    a multiple, that is impossible to miss — `1.0` says it.
    """
    paper = CusumEvent(product_id="BTC-USD", threshold_friction_mult=Decimal("1"))
    assert paper.threshold_pct == ROUND_TRIP_FRICTION_PCT


def test_the_filter_fires_on_the_bar_that_crosses() -> None:
    reading = cusum_read([Decimal("100"), Decimal("103"), Decimal("106")], Decimal("0.05"))
    assert reading.fired_up


def test_the_filter_resets_and_does_not_fire_again_on_the_next_bar() -> None:
    """**The load-bearing property.** Without the reset this is a trend detector.

    `S+` would climb monotonically through a rally and stay over the threshold for every later
    bar, so the rule would fire on every bar of the move — the every-bar evaluation it exists
    to replace, wearing a threshold.
    """
    crossed = [Decimal("100"), Decimal("103"), Decimal("106")]
    assert cusum_read(crossed, Decimal("0.05")).fired_up

    one_more_small_step = [*crossed, Decimal("106.1")]
    after = cusum_read(one_more_small_step, Decimal("0.05"))
    assert not after.fired_up, "the filter fired twice for one move — the reset is missing"
    assert after.s_plus < Decimal("0.05")


def test_a_flat_series_never_fires() -> None:
    assert not cusum_read([Decimal("100")] * 50, Decimal("0.05")).fired_up


def test_the_downward_side_fires_independently() -> None:
    reading = cusum_read([Decimal("100"), Decimal("97"), Decimal("94")], Decimal("0.05"))
    assert reading.fired_down
    assert not reading.fired_up


def test_a_non_positive_close_is_skipped_rather_than_dividing_by_zero() -> None:
    """A zero or negative close is not a price; it must not take the filter out with it."""
    reading = cusum_read(
        [Decimal("0"), Decimal("100"), Decimal("106")], Decimal("0.05")
    )
    assert reading.fired_up


# -- the rule -----------------------------------------------------------------------------------


def test_a_rally_past_the_threshold_produces_a_long_setup() -> None:
    rule = CusumEvent(product_id="BTC-USD")

    setup = rule.detect(_firing())

    assert isinstance(setup, Setup)
    assert setup.direction == "long"
    assert setup.stop < setup.entry < setup.target
    assert setup.context["friction_mult"] == 2.0


def test_a_move_smaller_than_the_threshold_declines_and_says_how_far_off(  ) -> None:
    """`signals=0` alone cannot distinguish "nothing happened" from "almost fired"."""
    rule = CusumEvent(product_id="BTC-USD")

    assert rule.detect({Granularity.ONE_HOUR: _flat_then(0.05, bars=4)}) is None
    assert rule.last_rejection is not None
    assert rule.last_rejection["gate"] == "cusum_threshold"
    assert rule.last_rejection["threshold_pct"] == 0.05
    assert 0 < rule.last_rejection["s_plus"] < 0.05


def test_raising_the_multiple_refuses_a_move_the_lower_one_took() -> None:
    """The knob does what it says, in the direction that matters: a higher multiple demands a
    bigger move before paying the same round trip."""
    # 0.6% x 5 bars = 3.0%: past one round trip (2.5%) and nowhere near four (10%), crossing
    # on the final bar so the lower threshold genuinely fires rather than having fired earlier.
    candles = {Granularity.ONE_HOUR: _flat_then(0.6, bars=5)}

    assert (
        CusumEvent(product_id="BTC-USD", threshold_friction_mult=Decimal("1")).detect(candles)
        is not None
    )
    assert (
        CusumEvent(product_id="BTC-USD", threshold_friction_mult=Decimal("4")).detect(candles)
        is None
    )


def test_insufficient_history_declines_with_the_count() -> None:
    rule = CusumEvent(product_id="BTC-USD")

    assert rule.detect({Granularity.ONE_HOUR: _candles([100.0] * 20)}) is None
    assert rule.last_rejection is not None
    assert rule.last_rejection["gate"] == "insufficient_history"


def test_a_rule_configured_for_hourly_never_decides_on_daily_bars() -> None:
    """An absent key declines as insufficient history rather than falling back — a quiet
    fallback would re-gate the rule on a clock nobody configured."""
    rule = CusumEvent(product_id="BTC-USD", granularity=Granularity.ONE_HOUR)

    assert rule.detect({Granularity.ONE_DAY: _flat_then(1.2, bars=5)}) is None
    assert rule.last_rejection is not None
    assert rule.last_rejection["gate"] == "insufficient_history"


def test_the_exit_is_the_same_filter_on_the_other_side() -> None:
    """Symmetric on purpose: the entry's claim is that a move of this size is the smallest one
    worth paying for, and the identical claim in reverse is the smallest worth exiting on. A
    different exit threshold would be a second free parameter with no evidence behind it."""
    rule = CusumEvent(product_id="BTC-USD")
    held = Setup(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=Decimal("115"),
        context={},
        ts=0,
    )

    falling = {Granularity.ONE_HOUR: _flat_then(-1.2, bars=5)}
    assert rule.exit_signal(held, falling) is True
    assert rule.exit_signal(held, _firing()) is False


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lookback": 1}, "lookback"),
        ({"threshold_friction_mult": Decimal("0")}, "threshold_friction_mult"),
        ({"atr_period": 0}, "atr_period"),
        ({"atr_stop_mult": Decimal("0")}, "atr_stop_mult"),
        ({"target_rr": Decimal("0")}, "target_rr"),
    ],
)
def test_a_nonsensical_parameter_is_refused_at_construction(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        CusumEvent(product_id="BTC-USD", **kwargs)


def test_the_threshold_percentage_is_derived_and_never_persisted() -> None:
    """A rule that stored the percentage would keep answering 2.5% after a fee change that made
    2.5% mean something else. The stored knob is the MULTIPLE."""
    rule = CusumEvent(product_id="BTC-USD")

    assert "threshold_pct" not in rule.describe()["params"]
    assert "threshold_friction_mult" in rule.describe()["params"]
