"""The DCA cadence expression has one home (`is_cadence_day`), shared by the rule that places the
buy and the console that says when the next one is due."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.dca import Dca, is_cadence_day, next_cadence_day_ts
from keel.types import Candle, Granularity

_DAY = 86_400


def _candle(day: int) -> Candle:
    p = Decimal("100")
    return Candle(ts=day * _DAY, open=p, high=p, low=p, close=p, volume=Decimal("1"))


def test_is_cadence_day_is_the_expression_detect_keys_off() -> None:
    for cadence in (1, 7, 30):
        for day in (0, 1, 6, 7, 29, 30, 31, 97, 98, 105, 19_000):
            for offset in (0, 1, 3_600, _DAY - 1):
                ts = day * _DAY + offset
                assert is_cadence_day(ts, cadence) is ((ts // _DAY) % cadence == 0)


def test_detect_fires_exactly_when_is_cadence_day_says_so() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    fired = []
    for d in range(98, 113):
        setup = rule.detect({Granularity.ONE_DAY: [_candle(day) for day in range(d - 3, d + 1)]})
        assert (setup is not None) is is_cadence_day(d * _DAY, 7)
        fired.append(setup is not None)
    # the spread must actually cross boundaries (98, 105, 112), or this passes by vacuum
    assert fired.count(True) == 3 and fired.count(False) == 12


def test_next_cadence_day_ts_is_today_on_a_cadence_day_and_the_next_boundary_otherwise() -> None:
    assert next_cadence_day_ts(105 * _DAY + 3600, 7) == 105 * _DAY
    assert next_cadence_day_ts(100 * _DAY, 7) == 105 * _DAY
    ts = 123 * _DAY + 777
    assert next_cadence_day_ts(ts, 1) == (ts // _DAY) * _DAY


def test_the_helpers_refuse_a_non_positive_cadence() -> None:
    for bad in (0, -1):
        with pytest.raises(ValueError):
            is_cadence_day(0, bad)
        with pytest.raises(ValueError):
            next_cadence_day_ts(0, bad)
