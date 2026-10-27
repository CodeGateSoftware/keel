"""#1010: a DCA window vetoed by a guard is retried on later fresh bars of the same window.

`Dca.detect` stays pure: the retry reads a caller-supplied `DcaWindowLedger`. With no ledger
(backtest, account sim, paper) behaviour is exactly the cadence-only rule."""

from __future__ import annotations

from decimal import Decimal

from keel.strategy.rules.dca import Dca, DcaWindowLedger
from keel.types import Candle, Granularity


def _candle(day: int) -> Candle:
    p = Decimal("100")
    return Candle(ts=day * 86_400, open=p, high=p, low=p, close=p, volume=Decimal("1"))


def _detect(rule, bar_day):
    return rule.detect({Granularity.ONE_DAY: [_candle(d) for d in range(85, bar_day + 1)]})


def test_without_a_ledger_nothing_changes_off_cadence() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    assert rule.window_ledger is None
    assert _detect(rule, 99) is None
    setup = _detect(rule, 98)
    assert setup is not None and "dca_retry" not in setup.context


def test_a_signalled_unsatisfied_window_retries_on_every_later_bar_of_that_window() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=None, signalled_bar_days=frozenset({98})
    )
    for d in range(99, 105):
        setup = _detect(rule, d)
        assert setup is not None, d
        assert setup.context["dca_retry"] is True
        assert setup.context["window_start_ts"] == 98 * 86_400
    nxt = _detect(rule, 105)
    assert nxt is not None and nxt.context["dca_retry"] is False


def test_a_satisfied_window_never_fires_again_even_on_its_own_cadence_bar() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=98, signalled_bar_days=frozenset({98})
    )
    for d in range(98, 105):
        assert _detect(rule, d) is None, d
    assert rule.last_rejection == {"gate": "dca_window_satisfied", "window_start_ts": 98 * 86_400}


def test_an_unsignalled_window_never_retries() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(last_counted_bar_day=None, signalled_bar_days=frozenset())
    assert _detect(rule, 100) is None
    assert rule.last_rejection == {"gate": "dca_off_cadence", "window_start_ts": 98 * 86_400}
    assert _detect(rule, 98) is not None


def test_the_bars_own_signal_does_not_justify_a_retry_on_that_bar() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=None, signalled_bar_days=frozenset({100})
    )
    assert _detect(rule, 100) is None


def test_a_previous_windows_signal_does_not_retry_into_this_one() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=None, signalled_bar_days=frozenset({91})
    )
    assert _detect(rule, 99) is None


def test_cadence_one_buys_every_day_with_the_ledger_on() -> None:
    rule = Dca("BTC-USD", cadence_days=1)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=98, signalled_bar_days=frozenset({98})
    )
    assert _detect(rule, 99) is not None


def test_a_missed_previous_window_is_reported_on_the_next_cadence_bar_not_back_filled() -> None:
    rule = Dca("BTC-USD", cadence_days=7)
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=None, signalled_bar_days=frozenset({98})
    )
    setup = _detect(rule, 105)
    assert setup is not None and setup.context["previous_window_missed_ts"] == 98 * 86_400
    rule.window_ledger = DcaWindowLedger(
        last_counted_bar_day=99, signalled_bar_days=frozenset({98})
    )
    again = _detect(rule, 105)
    assert again is not None and "previous_window_missed_ts" not in again.context
