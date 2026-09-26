"""Tests for keel.strategy.rules.dca: the DCA / dip-buy backbone rule (spec §8 rule 3,
§10.8/§12.1). Scheduled accumulation, market-buy, no stop, scaled up on dips from recent high,
never exits on signal.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.base import Setup
from keel.strategy.rules.dca import Dca
from keel.types import Candle, Granularity

_DAY = 86_400


def _candle(day: int, price: str, high: str | None = None) -> Candle:
    p = Decimal(price)
    h = Decimal(high) if high is not None else p
    return Candle(
        ts=day * _DAY,
        open=p,
        high=h,
        low=p,
        close=p,
        volume=Decimal("1"),
    )


class TestDcaCadence:
    def test_cadence_boundary_emits_buy_setup_for_budget(self) -> None:
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        candles = [_candle(day=0, price="100")]
        setup = rule.detect({Granularity.ONE_DAY: candles})

        assert isinstance(setup, Setup)
        assert setup.product_id == "BTC-USD"
        assert setup.direction == "long"
        assert setup.entry == Decimal("100")
        assert setup.context["size_usd"] == Decimal("50")
        assert setup.context["order_class"] == "dca"
        assert setup.context["entry_type"] == "market"

    def test_off_cadence_returns_none(self) -> None:
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        candles = [_candle(day=1, price="100")]
        assert rule.detect({Granularity.ONE_DAY: candles}) is None

    def test_no_daily_candles_returns_none(self) -> None:
        rule = Dca(product_id="BTC-USD")
        assert rule.detect({}) is None
        assert rule.detect({Granularity.ONE_DAY: []}) is None

    def test_second_cadence_boundary_also_fires(self) -> None:
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        candles = [_candle(day=d, price="100") for d in range(8)]  # day 7 is the next boundary
        setup = rule.detect({Granularity.ONE_DAY: candles})
        assert setup is not None


class TestDcaDipScaling:
    def test_no_dip_sizes_exactly_at_budget(self) -> None:
        rule = Dca(
            product_id="BTC-USD",
            cadence_days=1,
            budget_usd=Decimal("50"),
            dip_bonus_pct=Decimal("2"),
        )
        candles = [_candle(day=0, price="100", high="100")]
        setup = rule.detect({Granularity.ONE_DAY: candles})
        assert setup is not None
        assert setup.context["drawdown_pct"] == Decimal("0")
        assert setup.context["size_usd"] == Decimal("50")

    def test_deep_dip_sizes_larger_than_budget(self) -> None:
        rule = Dca(
            product_id="BTC-USD",
            cadence_days=1,
            budget_usd=Decimal("50"),
            dip_bonus_pct=Decimal("2"),
        )
        # recent high 200, latest close 100 -> 50% drawdown -> size = 50 * (1 + 2*50/100) = 100
        candles = [
            _candle(day=0, price="150", high="200"),
            _candle(day=1, price="100", high="100"),
        ]
        setup = rule.detect({Granularity.ONE_DAY: candles})
        assert setup is not None
        assert setup.context["drawdown_pct"] == Decimal("50")
        assert setup.context["size_usd"] == Decimal("100")
        assert setup.context["size_usd"] > rule.params["budget_usd"]

    def test_deeper_dip_sizes_larger_than_shallow_dip(self) -> None:
        rule = Dca(
            product_id="BTC-USD",
            cadence_days=1,
            budget_usd=Decimal("50"),
            dip_bonus_pct=Decimal("2"),
        )
        shallow = [_candle(day=0, price="100", high="110")]
        deep = [_candle(day=0, price="100", high="200")]

        shallow_setup = rule.detect({Granularity.ONE_DAY: shallow})
        deep_setup = rule.detect({Granularity.ONE_DAY: deep})

        assert shallow_setup is not None
        assert deep_setup is not None
        assert deep_setup.context["size_usd"] > shallow_setup.context["size_usd"]
        assert shallow_setup.context["size_usd"] > rule.params["budget_usd"]

    def test_zero_dip_bonus_ignores_drawdown(self) -> None:
        rule = Dca(
            product_id="BTC-USD",
            cadence_days=1,
            budget_usd=Decimal("50"),
            dip_bonus_pct=Decimal("0"),
        )
        candles = [_candle(day=0, price="50", high="200")]
        setup = rule.detect({Granularity.ONE_DAY: candles})
        assert setup is not None
        assert setup.context["size_usd"] == Decimal("50")


class TestDcaNoStopAccumulation:
    def test_setup_has_no_meaningful_stop(self) -> None:
        rule = Dca(product_id="BTC-USD", cadence_days=1, budget_usd=Decimal("50"))
        candles = [_candle(day=0, price="100")]
        setup = rule.detect({Granularity.ONE_DAY: candles})
        assert setup is not None
        assert setup.stop == Decimal("0")
        assert setup.target == setup.entry
        assert setup.context["no_stop"] is True
        # rr degrades gracefully to 0 rather than raising for the no-stop sentinel.
        assert setup.rr == Decimal("0")


class TestDcaExitSignal:
    def test_exit_signal_always_false(self) -> None:
        rule = Dca(product_id="BTC-USD")
        held = Setup(
            product_id="BTC-USD",
            direction="long",
            entry=Decimal("100"),
            stop=Decimal("0"),
            target=Decimal("100"),
            context={},
            ts=0,
        )
        assert rule.exit_signal(held, {Granularity.ONE_DAY: []}) is False
        candles = [_candle(day=100, price="9999")]
        assert rule.exit_signal(held, {Granularity.ONE_DAY: candles}) is False


class TestDcaDescribe:
    def test_describe_returns_name_and_params(self) -> None:
        rule = Dca(
            product_id="BTC-USD",
            cadence_days=7,
            budget_usd=Decimal("50"),
            dip_bonus_pct=Decimal("1.5"),
        )
        described = rule.describe()
        assert described["name"] == "dca"
        assert described["params"]["product_id"] == "BTC-USD"
        assert described["params"]["cadence_days"] == 7
        assert described["params"]["budget_usd"] == Decimal("50")
        assert described["params"]["dip_bonus_pct"] == Decimal("1.5")


class TestDcaValidation:
    def test_rejects_non_positive_cadence(self) -> None:
        with pytest.raises(ValueError):
            Dca(product_id="BTC-USD", cadence_days=0)

    def test_rejects_non_positive_budget(self) -> None:
        with pytest.raises(ValueError):
            Dca(product_id="BTC-USD", budget_usd=Decimal("0"))


_HOUR = 3_600


def _hour_bar(ts: int, price: str) -> Candle:
    p = Decimal(price)
    return Candle(ts=ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))


class TestDcaDecidesOnCompletedDaysOnly:
    """#821: the account sim hands `detect` a daily series whose last bar is the CURRENT,
    still-forming day -- stored with the completed day's OHLC. `Dca` must not read it, exactly as
    `TurtleBreakout` doesn't (`rules.base.completed_days`)."""

    # Day 7 is a cadence day for cadence_days=7 (7 % 7 == 0); day 8 is not.

    def test_forming_day_close_and_high_are_ignored_at_hour_zero(self) -> None:
        """Hour 0 of day 8: day 7 (cadence) is the last COMPLETED day, day 8 is forming and its
        stored close/high (999) are the future. The buy must be priced off day 7's close and its
        dip measured against a high that excludes day 8."""
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        daily = [_candle(day=d, price="100") for d in range(8)] + [
            _candle(day=8, price="999", high="999")
        ]
        hourly = [_hour_bar(8 * _DAY, "100")]  # the 00:00-01:00 bar of day 8

        setup = rule.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily})

        assert setup is not None
        assert setup.ts == 7 * _DAY
        assert setup.entry == Decimal("100")
        assert setup.context["recent_high"] == Decimal("100")

    def test_a_forming_cadence_day_does_not_fire_before_it_closes(self) -> None:
        """Hour 0 of day 7 (cadence): day 7 has not closed, day 6 is off-cadence -> no buy yet."""
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        daily = [_candle(day=d, price="100") for d in range(8)]  # day 7 is forming
        hourly = [_hour_bar(7 * _DAY, "100")]

        assert rule.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily}) is None

    def test_live_shaped_input_keeps_the_closed_last_day(self) -> None:
        """Live (`agent.run_once`) persists only CLOSED candles: the last daily bar is day 7,
        already closed, and the hourly series is past it. That bar must NOT be dropped -- live
        behaviour is unchanged by the forming-day guard."""
        rule = Dca(product_id="BTC-USD", cadence_days=7, budget_usd=Decimal("50"))
        daily = [_candle(day=d, price="100") for d in range(7)] + [_candle(day=7, price="120")]
        for hourly_ts in (8 * _DAY, 8 * _DAY + _HOUR):  # 00:00 and 01:00 bars of day 8
            hourly = [_hour_bar(hourly_ts - _HOUR, "120"), _hour_bar(hourly_ts, "120")]

            setup = rule.detect({Granularity.ONE_HOUR: hourly, Granularity.ONE_DAY: daily})

            assert setup is not None
            assert setup.ts == 7 * _DAY
            assert setup.entry == Decimal("120")
