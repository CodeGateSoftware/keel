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
