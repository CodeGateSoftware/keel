"""Tests for halal_cb.strategy.backtest: the historical backtest engine.

Drives the backtester with test-only `Rule` subclasses (per the Phase 2 plan, this
module must not depend on any concrete rule implementation) against small,
hand-built candle series where the expected outcome is known exactly.
"""

from __future__ import annotations

from decimal import Decimal

from halal_cb.strategy.backtest import BacktestResult, backtest
from halal_cb.strategy.rules.base import Rule, Setup
from halal_cb.types import Candle, Granularity, Side


def _candle(ts: int, o: str, h: str, l: str, c: str) -> Candle:  # noqa: E741 - matches OHLC convention
    return Candle(
        ts=ts, open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=Decimal(0)
    )


class _ScriptedRule(Rule):
    """Fires exactly once, on the bar whose ts == trigger_ts. Never exits on signal.

    A minimal, test-only stand-in for a real rule (e.g. pullback-continuation) that
    lets us drive the backtester deterministically without depending on any concrete
    rule implementation.
    """

    name = "scripted"
    params: dict = {}

    def __init__(self, trigger_ts: int, entry: Decimal, stop: Decimal, target: Decimal) -> None:
        self.trigger_ts = trigger_ts
        self.entry = entry
        self.stop = stop
        self.target = target

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        window = next(iter(candles_by_tf.values()))
        latest = window[-1]
        if latest.ts != self.trigger_ts:
            return None
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=self.entry,
            stop=self.stop,
            target=self.target,
            context={},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class _AlwaysOnRule(Rule):
    """Would fire a signal on EVERY bar it's asked, if the backtester let it.

    Used to prove overlapping signals are not double-counted: the backtester must
    only call `detect()` while flat (no pending or open position), so a persistent
    "always true" condition must still yield only sequential, non-overlapping trades.
    """

    name = "always_on"
    params: dict = {}

    def __init__(self, entry: Decimal, stop: Decimal, target: Decimal) -> None:
        self.entry = entry
        self.stop = stop
        self.target = target

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        window = next(iter(candles_by_tf.values()))
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=self.entry,
            stop=self.stop,
            target=self.target,
            context={},
            ts=window[-1].ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class TestBacktestResultShape:
    def test_empty_series_yields_empty_result(self) -> None:
        rule = _ScriptedRule(trigger_ts=999, entry=Decimal(1), stop=Decimal(1), target=Decimal(1))
        result = backtest(rule, [])
        assert isinstance(result, BacktestResult)
        assert result.trades == []
        assert result.n_trades == 0
        assert result.win_rate == 0.0
        assert result.avg_win == Decimal(0)
        assert result.avg_loss == Decimal(0)
        assert result.expectancy == Decimal(0)
        assert result.profit_factor == Decimal(0)
        assert result.max_drawdown == Decimal(0)
        assert result.max_losing_streak == 0
        assert result.avg_mfe == Decimal(0)
        assert result.avg_mae == Decimal(0)


class TestKnownWinningTrade:
    """A rule fires once; price fills the entry, then runs cleanly to target."""

    def _candles(self) -> list[Candle]:
        return [
            _candle(0, "100", "101", "99", "100"),  # baseline; rule doesn't fire yet
            _candle(60, "104", "106", "103", "105"),  # trigger bar: rule fires here
            _candle(120, "105", "112", "104", "108"),  # fill bar: touches entry(110) only
            _candle(180, "111", "135", "109", "132"),  # exit bar: touches target(130) only
        ]

    def _rule(self) -> _ScriptedRule:
        return _ScriptedRule(
            trigger_ts=60, entry=Decimal(110), stop=Decimal(95), target=Decimal(130)
        )

    def test_expectancy_positive_and_win_rate_correct(self) -> None:
        result = backtest(self._rule(), self._candles())

        assert result.n_trades == 1
        assert result.trades[0].outcome == "win"
        assert result.win_rate == 1.0
        assert result.expectancy > Decimal(0)
        assert result.profit_factor == Decimal("Infinity")

    def test_mfe_and_mae_recorded_correctly(self) -> None:
        result = backtest(self._rule(), self._candles())

        trade = result.trades[0]
        entry_fill = Decimal(110) * Decimal("1.0005")
        expected_mfe = Decimal("135") - entry_fill  # highest high (exit bar) minus entry fill
        expected_mae = entry_fill - Decimal("104")  # lowest low (fill bar) minus entry fill
        assert trade.mfe == expected_mfe
        assert trade.mae == expected_mae
        assert result.avg_mfe == expected_mfe
        assert result.avg_mae == expected_mae

    def test_fees_and_slippage_applied_on_entry_and_exit(self) -> None:
        result = backtest(
            self._rule(), self._candles(), fee_pct=Decimal("0.006"), slippage_pct=Decimal("0.0005")
        )

        trade = result.trades[0]
        entry_fill = Decimal(110) * Decimal("1.0005")
        exit_fill = Decimal(130) * (Decimal(1) - Decimal("0.0005"))
        entry_fee = entry_fill * Decimal("0.006")
        exit_fee = exit_fill * Decimal("0.006")
        expected_pnl = (exit_fill - entry_fill) - entry_fee - exit_fee

        assert trade.entry == entry_fill
        assert trade.exit == exit_fill
        assert trade.pnl == expected_pnl
        assert trade.side == Side.BUY
        assert result.expectancy == expected_pnl


class TestIntrabarResolutionEntryVsStop:
    """A fill-seeking bar's range spans both the entry and the stop level: ambiguous
    without finer data. `finer_candles` must resolve the true order of events.
    """

    def _candles(self) -> list[Candle]:
        return [
            _candle(0, "100", "101", "99", "100"),
            _candle(60, "104", "106", "103", "105"),  # trigger bar
            # fill-seeking bar: spans BOTH entry(110) and stop(100) -> ambiguous
            _candle(120, "105", "115", "95", "112"),
            _candle(180, "112", "135", "111", "132"),
        ]

    def _rule(self) -> _ScriptedRule:
        return _ScriptedRule(
            trigger_ts=60, entry=Decimal(110), stop=Decimal(100), target=Decimal(130)
        )

    def test_finer_candles_showing_stop_first_invalidates_the_trade(self) -> None:
        finer = [
            # within [120, 180): price dips to touch stop(100) before ever reaching entry(110)
            _candle(125, "105", "106", "99", "100"),
            _candle(140, "100", "112", "99", "111"),
        ]
        result = backtest(self._rule(), self._candles(), finer_candles=finer)

        # invalidated: the setup never resulted in a real fill, so no trade is recorded
        assert result.n_trades == 0
        assert result.trades == []
        assert result.expectancy == Decimal(0)

    def test_finer_candles_showing_entry_first_fills_and_continues(self) -> None:
        finer = [
            # within [120, 180): price rises to touch entry(110) before ever touching stop(100)
            _candle(125, "105", "111", "104", "110"),
            _candle(140, "110", "116", "109", "112"),
        ]
        result = backtest(self._rule(), self._candles(), finer_candles=finer)

        assert result.n_trades == 1
        assert result.trades[0].outcome == "win"

    def test_ambiguous_bar_without_finer_candles_is_conservatively_invalidated(self) -> None:
        result = backtest(self._rule(), self._candles(), finer_candles=None)

        assert result.n_trades == 0
        assert result.trades == []


class TestNoOverlap:
    def test_overlapping_signals_are_not_double_counted(self) -> None:
        rule = _AlwaysOnRule(entry=Decimal(110), stop=Decimal(95), target=Decimal(130))
        candles = [
            _candle(0, "100", "101", "99", "100"),  # flat: rule fires -> pending set
            _candle(60, "105", "112", "104", "108"),  # fill bar: touches entry(110) only
            _candle(120, "112", "132", "110", "128"),  # exit bar: touches target(130) only
            # flat again here; rule fires a fresh (non-overlapping) signal, but it
            # never fills within the remaining data, so no second trade is recorded
            _candle(180, "128", "129", "127", "128"),
        ]
        result = backtest(rule, candles)

        assert result.n_trades == 1
        assert result.trades[0].outcome == "win"

    def test_open_position_blocks_new_detection_until_closed(self) -> None:
        rule = _AlwaysOnRule(entry=Decimal(110), stop=Decimal(95), target=Decimal(130))
        candles = [
            _candle(0, "100", "101", "99", "100"),
            _candle(60, "105", "112", "104", "108"),  # fills
            _candle(120, "108", "109", "107", "108"),  # holding: no stop/target touch
            _candle(180, "108", "109", "107", "108"),  # still holding
        ]
        result = backtest(rule, candles)

        # never hit stop or target within the series -> recorded as still-open, and
        # NOT counted toward win/loss stats
        assert result.n_trades == 0
        assert len(result.trades) == 1
        assert result.trades[0].outcome == "open"
        assert result.trades[0].exit is None
