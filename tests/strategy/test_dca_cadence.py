"""The DCA cadence expression has one home (`is_cadence_day`), shared by the rule that places the
buy and the console that says when the next one is due."""

from __future__ import annotations

from decimal import Decimal

import pytest

from keel.strategy.rules.dca import (
    Dca,
    DcaCatchUp,
    is_cadence_day,
    next_cadence_day_ts,
    window_start_day,
)
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


# -- catch-up until the next window (#1010) ---------------------------------------------------

_K = 1_400  # an arbitrary window index, far from day 0 so no lookback edge is involved


def _bars_ending(day: int) -> dict[Granularity, list[Candle]]:
    return {Granularity.ONE_DAY: [_candle(d) for d in range(day - 3, day + 1)]}


def _rule(cadence: int, catch_up: DcaCatchUp | None) -> Dca:
    rule = Dca("BTC-USD", cadence_days=cadence)
    rule.catch_up = catch_up
    return rule


def test_window_start_day_is_the_last_cadence_day_on_or_before_the_day() -> None:
    assert window_start_day(14 * _K, 14) == 14 * _K
    assert window_start_day(14 * _K + 13, 14) == 14 * _K
    assert window_start_day(14 * _K + 14, 14) == 14 * (_K + 1)
    for day in range(98, 120):
        assert is_cadence_day(window_start_day(day, 7) * _DAY, 7)


def test_no_catch_up_state_is_unchanged_behaviour() -> None:
    """Backtests and the sim never set `catch_up`: off-window days stay silent."""
    assert Dca("BTC-USD", cadence_days=7).catch_up is None
    assert Dca("BTC-USD", cadence_days=7).detect(_bars_ending(7 * _K + 3)) is None
    setup = Dca("BTC-USD", cadence_days=7).detect(_bars_ending(7 * _K))
    assert setup is not None
    assert setup.context["catch_up"] is False and setup.context["window_start_day"] == 7 * _K


def test_catch_up_fires_on_a_missed_window_day() -> None:
    rule = _rule(14, DcaCatchUp(since_day=0, filled_window_day=None, in_flight=False))
    setup = rule.detect(_bars_ending(14 * _K + 5))
    assert setup is not None
    assert setup.context["catch_up"] is True
    assert setup.context["window_start_day"] == 14 * _K
    assert setup.ts == (14 * _K + 5) * _DAY, "sized and stamped from the catch-up day's bar"


def test_catch_up_sizes_from_the_catch_up_days_bar_not_the_window_opening_bar() -> None:
    w = 14 * _K
    bars = [_candle(d) for d in range(w - 3, w + 6)]
    # The window-open bar is at 100; the catch-up day's bar closes at 80 (20% below the high).
    last = bars[-1]
    p = Decimal("80")
    bars[-1] = Candle(ts=last.ts, open=p, high=p, low=p, close=p, volume=Decimal("1"))
    rule = Dca("BTC-USD", cadence_days=14, dip_bonus_pct=Decimal("1"))
    rule.catch_up = DcaCatchUp(since_day=0, filled_window_day=None, in_flight=False)
    setup = rule.detect({Granularity.ONE_DAY: bars})
    assert setup is not None
    assert setup.entry == Decimal("80")
    assert setup.context["drawdown_pct"] == Decimal("20")
    assert setup.context["size_usd"] == Decimal("50") * Decimal("1.2")


def test_catch_up_stops_once_the_window_filled() -> None:
    rule = _rule(14, DcaCatchUp(0, filled_window_day=14 * _K, in_flight=False))
    assert rule.detect(_bars_ending(14 * _K + 5)) is None
    assert rule.detect(_bars_ending(14 * _K + 13)) is None


def test_window_open_day_after_fill_in_same_window_does_not_rebuy() -> None:
    """Review Focus 4: a cycle that re-runs on the window-open bar after the buy filled."""
    rule = _rule(14, DcaCatchUp(0, filled_window_day=14 * _K, in_flight=False))
    assert rule.detect(_bars_ending(14 * _K)) is None


def test_a_fill_in_the_previous_window_does_not_block_this_one() -> None:
    rule = _rule(14, DcaCatchUp(0, filled_window_day=14 * (_K - 1), in_flight=False))
    assert rule.detect(_bars_ending(14 * _K)) is not None
    assert rule.detect(_bars_ending(14 * _K + 3)) is not None


def test_in_flight_order_blocks_catch_up() -> None:
    """Two buys are never in flight: a resting BUY blocks the retry AND the window-open buy."""
    rule = _rule(14, DcaCatchUp(0, filled_window_day=None, in_flight=True))
    assert rule.detect(_bars_ending(14 * _K + 5)) is None
    assert rule.detect(_bars_ending(14 * _K)) is None


def test_rule_promoted_mid_window_waits_for_next_window() -> None:
    """Review Focus 3: the window's buying cycle (day w + 1) ran before the rule went live."""
    rule = _rule(14, DcaCatchUp(since_day=14 * _K + 4, filled_window_day=None, in_flight=False))
    assert rule.detect(_bars_ending(14 * _K + 6)) is None
    assert rule.detect(_bars_ending(14 * (_K + 1))) is not None


def test_rule_live_on_the_windows_buying_day_still_catches_up() -> None:
    """since_day == w + 1: the rule was live on the day the window's buying cycle ran."""
    rule = _rule(14, DcaCatchUp(since_day=14 * _K + 1, filled_window_day=None, in_flight=False))
    setup = rule.detect(_bars_ending(14 * _K + 6))
    assert setup is not None and setup.context["catch_up"] is True


def test_next_window_open_day_is_a_normal_buy() -> None:
    rule = _rule(14, DcaCatchUp(0, filled_window_day=14 * _K, in_flight=False))
    setup = rule.detect(_bars_ending(14 * (_K + 1)))
    assert setup is not None
    assert setup.context["catch_up"] is False
    assert setup.context["window_start_day"] == 14 * (_K + 1)


def test_a_catch_up_with_no_since_day_never_catches_up() -> None:
    """A rule with no `promoted_at`/`created_at` is never read as "live since forever": only the
    window-open day buys, and the ledger guards still apply to it."""
    rule = _rule(14, DcaCatchUp(since_day=None, filled_window_day=None, in_flight=False))
    assert rule.detect(_bars_ending(14 * _K + 5)) is None
    assert rule.detect(_bars_ending(14 * _K)) is not None
    rule.catch_up = DcaCatchUp(since_day=None, filled_window_day=14 * _K, in_flight=False)
    assert rule.detect(_bars_ending(14 * _K)) is None


def test_every_day_of_a_missed_window_fires_until_the_next_one() -> None:
    """The spread: with nothing filled, every day of the window buys (catch-up after day w)."""
    rule = _rule(7, DcaCatchUp(since_day=0, filled_window_day=None, in_flight=False))
    flags = []
    for d in range(7 * _K, 7 * _K + 7):
        setup = rule.detect(_bars_ending(d))
        assert setup is not None
        flags.append(setup.context["catch_up"])
    assert flags == [False] + [True] * 6
