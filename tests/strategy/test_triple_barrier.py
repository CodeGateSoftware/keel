"""#342 -- triple-barrier exits with a vertical time stop, barriers sized to friction.

Two things are new here and everything below holds one of them:

* **The vertical barrier.** No other shipped rule has a holding-duration exit. It closes at the
  bar's CLOSE regardless of price, which is executable under keel's market fills — a resting
  order at a horizontal barrier is not, which is why that half of the source's method does not
  transfer and this half does.
* **Barriers sized to the PRODUCT'S OWN round trip.** The source's ±2.5–5% sit at or below one
  round trip on this venue, so transplanted as percentages they are mechanically dead. A fixed
  percentage is also wrong per asset: since #259 the backtest prices thin books up to 183.8bp
  per leg, so the same 5% is four round trips on BTC and barely one on the corpus tail.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.backtest import SLIPPAGE_CAP_PCT, SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT
from keel.strategy.rules.base import Setup
from keel.strategy.rules.triple_barrier import TripleBarrier, per_product_round_trip
from keel.types import Candle, Granularity
from tests.strategy.rule_conformance import RuleConformanceTests

_HOUR = 3600


def _candles(closes: list[float], *, volume: float = 25_000_000.0) -> list[Candle]:
    out: list[Candle] = []
    for index, close in enumerate(closes):
        price = Decimal(str(close))
        out.append(
            Candle(
                ts=1_700_000_000 + index * _HOUR,
                open=price,
                high=price * Decimal("1.004"),
                low=price * Decimal("0.996"),
                close=price,
                volume=Decimal(str(volume)) / price,
            )
        )
    return out


def _flat_then(rise_pct: float, *, bars: int, flat: int = 760, volume: float = 25_000_000.0):
    """`flat` bars at 100 then `bars` moving by `rise_pct` — crossing on the FINAL bar, which is
    what the CUSUM reset requires (see `tests/strategy/test_cusum_event.py`)."""
    closes = [100.0] * flat
    price = 100.0
    for _ in range(bars):
        price *= 1 + rise_pct / 100
        closes.append(price)
    return _candles(closes, volume=volume)


def _firing() -> dict[Granularity, list[Candle]]:
    """A liquid product. The slippage model is anchored at $500M DAILY, so the floor needs
    ≥ 500M/day — 25M per hourly bar is 600M/day. One round trip is then 2 × 1.2% + 2 × 0.05% =
    2.5%, the default entry multiple of 2 makes the threshold 5%, and five 1.2% steps cross it
    on the final bar. (A first draft used 500k/bar = 12M/day and priced at 30.5bp, not the
    floor — the anchor is a daily figure and 12M/day is not liquid against it.)"""
    return {Granularity.ONE_HOUR: _flat_then(1.2, bars=5)}


class TestTripleBarrierConformance(RuleConformanceTests):
    def rule(self) -> TripleBarrier:
        return TripleBarrier(product_id="BTC-USD")

    def firing_candles(self) -> dict[Granularity, list[Candle]]:
        return _firing()


# -- per-product friction -----------------------------------------------------------------------


def test_a_liquid_product_prices_at_the_slippage_floor() -> None:
    liquid = _candles([100.0] * 720, volume=25_000_000.0)

    friction = per_product_round_trip(liquid, Granularity.ONE_HOUR)

    assert friction == 2 * TAKER_FEE_PCT + 2 * SLIPPAGE_FLOOR_PCT
    assert friction == Decimal("0.025")


def test_a_thin_product_prices_wider_than_a_liquid_one() -> None:
    """The whole reason the barrier is per-product: the same percentage is four round trips on
    BTC and barely one on the corpus tail."""
    liquid = per_product_round_trip(
        _candles([100.0] * 720, volume=25_000_000.0), Granularity.ONE_HOUR
    )
    thin = per_product_round_trip(_candles([100.0] * 720, volume=100.0), Granularity.ONE_HOUR)

    assert thin > liquid
    assert thin == 2 * TAKER_FEE_PCT + 2 * SLIPPAGE_CAP_PCT


def test_the_per_bar_statistic_is_scaled_to_a_daily_one() -> None:
    """**The unit bug this guards, which produces no error at all.**

    `median_daily_quote_volume` returns a PER-BAR median despite its name. `slippage_for_quote_
    volume` is anchored on $500M DAILY, so handing it the hourly figure unscaled reports every
    asset as maximally thin, clamps the whole universe to the 183.8bp cap, and makes every
    barrier four times too wide — silently.

    Asserted by the scaling's own consequence: the same bars read as ONE_DAY carry 1/24th the
    daily volume and must therefore price WIDER.
    """
    bars = _candles([100.0] * 720, volume=25_000_000.0)

    hourly = per_product_round_trip(bars, Granularity.ONE_HOUR)
    daily = per_product_round_trip(bars, Granularity.ONE_DAY)

    assert hourly < daily, "the per-bar statistic is not being scaled by bars-per-day"
    assert hourly == Decimal("0.025")


# -- the barriers -------------------------------------------------------------------------------


def test_the_barriers_are_multiples_of_the_products_round_trip() -> None:
    rule = TripleBarrier(product_id="BTC-USD")

    setup = rule.detect(_firing())

    assert setup is not None
    friction = Decimal("0.025")
    assert setup.target == setup.entry * (1 + Decimal("4") * friction)
    assert setup.stop == setup.entry * (1 - Decimal("2") * friction)
    assert setup.context["round_trip_pct"] == 0.025


def test_the_sources_own_barrier_would_be_inside_one_round_trip() -> None:
    """The finding that motivates friction-sizing. The paper's ±2.5–5% is one to two round
    trips here, so a 2.5% target pays for the trade and leaves nothing — a barrier that is
    mechanically dead rather than merely tight."""
    rule = TripleBarrier(product_id="BTC-USD")
    setup = rule.detect(_firing())

    assert setup is not None
    paper_target = setup.entry * Decimal("1.025")
    assert paper_target < setup.entry * (1 + Decimal("0.025")) + Decimal("0.0001")
    assert setup.target > paper_target, "the shipped barrier must be scaled several-fold"


def test_a_thin_product_gets_wider_barriers_than_a_liquid_one() -> None:
    liquid = TripleBarrier(product_id="BTC-USD").detect(
        {Granularity.ONE_HOUR: _flat_then(1.2, bars=5, volume=25_000_000.0)}
    )
    # A thin product's round trip is 2 x 1.2% + 2 x 183.8bp = 6.08%, so at entry_mult=1 the
    # threshold is 6.08% and three 3% steps (9%) cross it ON THE FINAL BAR. Four steps would
    # cross at the third and reset, and the last bar would decline -- the same reset property
    # `test_cusum_event.py` documents, and the same way it bites when writing a fixture.
    thin_rule = TripleBarrier(product_id="TON-USD", entry_friction_mult=Decimal("1"))
    thin = thin_rule.detect({Granularity.ONE_HOUR: _flat_then(3.0, bars=3, volume=100.0)})

    assert liquid is not None and thin is not None
    liquid_width = (liquid.target - liquid.entry) / liquid.entry
    thin_width = (thin.target - thin.entry) / thin.entry
    assert thin_width > liquid_width


# -- the vertical barrier -----------------------------------------------------------------------


def _held(ts: int) -> Setup:
    return Setup(
        product_id="BTC-USD",
        direction="long",
        entry=Decimal("100"),
        stop=Decimal("95"),
        target=Decimal("110"),
        context={},
        ts=ts,
    )


def test_the_vertical_barrier_closes_the_position_after_n_bars() -> None:
    rule = TripleBarrier(product_id="BTC-USD", max_holding_bars=24)
    series = _candles([100.0] * 100)
    entry_ts = series[50].ts

    assert rule.exit_signal(_held(entry_ts), {Granularity.ONE_HOUR: series[:75]}) is True


def test_the_vertical_barrier_does_not_fire_early() -> None:
    rule = TripleBarrier(product_id="BTC-USD", max_holding_bars=24)
    series = _candles([100.0] * 100)
    entry_ts = series[50].ts

    assert rule.exit_signal(_held(entry_ts), {Granularity.ONE_HOUR: series[:70]}) is False


def test_the_vertical_barrier_counts_BARS_not_wall_clock() -> None:
    """A gap in the candle history must not exit early.

    Counting elapsed seconds would fire the moment the clock passed N × bar-duration even if the
    venue produced no bars — an exit triggered by missing data rather than by elapsed trading.
    """
    rule = TripleBarrier(product_id="BTC-USD", max_holding_bars=24)
    dense = _candles([100.0] * 60)
    entry_ts = dense[10].ts
    # Twelve bars, spread across a month of wall-clock: far past 24 hours, only 12 bars.
    sparse = [
        Candle(
            ts=entry_ts + (index + 1) * 86_400 * 3,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("100"),
        )
        for index in range(12)
    ]

    assert rule.exit_signal(_held(entry_ts), {Granularity.ONE_HOUR: sparse}) is False


def test_the_horizontal_barriers_are_not_restated_by_the_exit_signal() -> None:
    """`exit_signal` owns the VERTICAL barrier alone. The stop and target ride on the `Setup`
    and are the backtester's to enforce; restating them here would be two mechanisms deciding
    one exit, and they would drift."""
    rule = TripleBarrier(product_id="BTC-USD", max_holding_bars=24)
    series = _candles([100.0] * 60)
    collapsed = [*series, *_candles([1.0] * 5)]

    assert rule.exit_signal(_held(series[50].ts), {Granularity.ONE_HOUR: collapsed[:56]}) is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lookback": 1},
        {"liquidity_bars": 0},
        {"entry_friction_mult": Decimal("0")},
        {"target_friction_mult": Decimal("0")},
        {"stop_friction_mult": Decimal("0")},
        {"max_holding_bars": 0},
    ],
)
def test_a_nonsensical_parameter_is_refused_at_construction(kwargs) -> None:
    with pytest.raises(ValueError):
        TripleBarrier(product_id="BTC-USD", **kwargs)


def test_a_stop_multiple_large_enough_to_invert_the_barriers_is_declined() -> None:
    """Unreachable inside the DECLARED space, reachable from a stored row.

    `param_space` caps `stop_friction_mult` at 4, and 4 × the widest friction (6.08% on a
    capped-slippage product) is 0.243 — comfortably above zero. But `rules add --params` and a
    persisted row accept any positive Decimal, so a multiple of 20 puts the stop at or below
    zero and inverts the barriers. A mutation deleting the guard survived until this test
    existed, and the honest reading was that the guard is not dead code — the declared space is
    just narrower than the constructor.
    """
    rule = TripleBarrier(
        product_id="TON-USD",
        entry_friction_mult=Decimal("1"),
        stop_friction_mult=Decimal("20"),
    )

    assert rule.detect({Granularity.ONE_HOUR: _flat_then(3.0, bars=3, volume=100.0)}) is None
    assert rule.last_rejection is not None
    assert rule.last_rejection["gate"] == "barriers_degenerate"
