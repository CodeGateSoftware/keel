"""Tests for keel.strategy.backtest: the historical backtest engine.

Drives the backtester with test-only `Rule` subclasses (per the Phase 2 plan, this
module must not depend on any concrete rule implementation) against small,
hand-built candle series where the expected outcome is known exactly.
"""

from __future__ import annotations

from decimal import Decimal

from keel_core.config import FeesConfig

from keel.strategy.backtest import TAKER_FEE_PCT, BacktestResult, backtest
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity, Side


def test_taker_fee_constant_tracks_the_config_schema_default() -> None:
    """`backtest.TAKER_FEE_PCT` and `FeesConfig.taker_pct` must not drift apart.

    They are deliberately two constants rather than one import: `backtest.py` keeps itself free
    of config-package coupling (the same reason it imports no concrete `Rule`). That freedom is
    only safe if drift is a CI failure rather than a discovery, which is what this test is --
    the library default and the value a config-bearing caller threads in must name the same
    rate, or the "which fee did this number use?" question has two answers again.
    """
    assert TAKER_FEE_PCT == FeesConfig().taker_pct


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

    Used to prove overlapping signals are not double-counted: the backtester must only call
    `detect()` while flat, so a persistent "always true" condition must still yield only
    sequential, non-overlapping trades. Note it is the OPEN POSITION that blocks re-detection --
    an unfilled pending setup does not, and is replaced each bar (#254).
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
            _candle(120, "105", "112", "104", "108"),  # fill bar: fills at its open (105)
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
        # #257: filled at the FILL BAR's open (105), not at the setup's quoted entry (110).
        entry_fill = Decimal(105) * Decimal("1.0005")
        expected_mfe = Decimal("135") - entry_fill  # highest high (exit bar) minus entry fill
        expected_mae = entry_fill - Decimal("104")  # lowest low (fill bar) minus entry fill
        assert trade.mfe == expected_mfe
        assert trade.mae == expected_mae
        assert result.avg_mfe == expected_mfe
        assert result.avg_mae == expected_mae

    def test_default_fee_is_the_taker_rate_not_the_maker_rate(self) -> None:
        """The simulator fills market-style at next-bar open, which is TAKER behaviour, so the
        default it charges must be the taker rate.

        Pinned as a *behavioural* assertion (the P&L the default actually produces), not just
        the constant's value: until #247 the default was the MAKER rate (0.006) while the fill
        model was taker, so every profit factor this project printed -- including the input to
        `promotion.can_promote` -- was priced at half the real cost of trading. The equality
        against an explicit `fee_pct=TAKER_FEE_PCT` is what fails if the default ever drifts
        back down; the inequality against the maker rate is what fails if the two are confused
        again.
        """
        default_run = backtest(self._rule(), self._candles())
        taker_run = backtest(self._rule(), self._candles(), fee_pct=TAKER_FEE_PCT)
        maker_run = backtest(self._rule(), self._candles(), fee_pct=Decimal("0.006"))

        assert default_run.trades[0].pnl == taker_run.trades[0].pnl
        assert default_run.trades[0].pnl != maker_run.trades[0].pnl
        # The maker rate flatters: half the cost, so strictly more profit.
        assert maker_run.trades[0].pnl > default_run.trades[0].pnl

    def test_fees_and_slippage_applied_on_entry_and_exit(self) -> None:
        result = backtest(
            self._rule(), self._candles(), fee_pct=Decimal("0.006"), slippage_pct=Decimal("0.0005")
        )

        trade = result.trades[0]
        # #257: entry is the fill bar's open plus slippage; the setup's 110 is informational.
        entry_fill = Decimal(105) * Decimal("1.0005")
        exit_fill = Decimal(130) * (Decimal(1) - Decimal("0.0005"))
        entry_fee = entry_fill * Decimal("0.006")
        exit_fee = exit_fill * Decimal("0.006")
        expected_pnl = (exit_fill - entry_fill) - entry_fee - exit_fee

        assert trade.entry == entry_fill
        assert trade.exit == exit_fill
        assert trade.pnl == expected_pnl
        assert trade.side == Side.BUY
        assert result.expectancy == expected_pnl


# NOTE: `TestIntrabarResolutionEntryVsStop` was deleted by #257. It exercised the resolution of
# a bar whose range spanned both the entry and the stop -- a question that only arises when an
# entry is a resting order seeking a level. Entries now fill at the next bar's open, so that
# ambiguity is unreachable and the code path it covered is gone. `_resolve_order` is still
# exercised for the stop-vs-target case, which remains real (see the shared exit block).


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


class _StaleThenReachableRule(Rule):
    """Emits an UNREACHABLE setup first, then a fillable one from `switch_ts` onward.

    The regression fixture for #254. The first setup's entry is never touched and neither is its
    stop, which is precisely the state that used to pin `pending` forever: the backtester's
    `pending is None` branch never ran again, `detect()` was never called again, and the second,
    fillable setup was never seen. Measured in the wild as `rsi_meanrev` on UNI-USD at
    oversold=35 -- 9 trades and a detector dead from November 2021, against 309 trades at the
    STRICTER oversold=30.
    """

    name = "stale_then_reachable"
    params: dict = {}

    def __init__(self, switch_ts: int) -> None:
        self.switch_ts = switch_ts
        self.detect_calls = 0

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        self.detect_calls += 1
        window = next(iter(candles_by_tf.values()))
        latest = window[-1]
        # Far above every price in the series, so neither entry nor stop is ever touched.
        entry, stop, target = (Decimal(1000), Decimal(900), Decimal(1100))
        if latest.ts >= self.switch_ts:
            entry, stop, target = (Decimal(110), Decimal(95), Decimal(130))
        return Setup(
            product_id="BTC-USD",
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context={},
            ts=latest.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        return False

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}


class TestEntryFillsAtNextBarOpen:
    """#257: production places market orders, so an entry fills at the next bar's open.

    This also subsumes #254. That defect was a setup whose entry level was never revisited
    pinning `pending` forever, which switched the detector off for the rest of the series
    (`rsi_meanrev` on UNI-USD at oversold=35: dead from November 2021, 9 trades against 309 at
    the STRICTER oversold=30). Under a market fill every pending resolves on the very next bar,
    so the state that caused it is now unreachable rather than merely handled.
    """

    def _candles(self) -> list[Candle]:
        return [
            _candle(0, "100", "101", "99", "100"),  # rule fires here
            _candle(60, "102", "103", "101", "102"),  # FILL bar: opens 102, never nears 1000
            _candle(120, "102", "103", "101", "102"),
        ]

    def test_setup_fills_at_the_next_bars_open_not_its_quoted_entry(self) -> None:
        """The entry level is never touched, and the trade happens anyway."""
        rule = _StaleThenReachableRule(switch_ts=10**9)  # always the unreachable setup
        result = backtest(rule, self._candles(), slippage_pct=Decimal("0.0005"))

        # Before #257 this was zero trades: entry(1000) was never touched, so nothing filled.
        assert len(result.trades) == 1
        assert result.trades[0].entry == Decimal(102) * Decimal("1.0005")

    def test_detector_cannot_be_frozen_by_an_unfilled_setup(self) -> None:
        """The #254 regression, restated for the model that makes it impossible.

        Bar 0 detects; bar 60 fills. Detection stops only because a position is open -- never
        because a stale setup is pinned.
        """
        rule = _StaleThenReachableRule(switch_ts=10**9)
        backtest(rule, self._candles())

        # Exactly one: bar 0 detects, bar 60 fills, bar 120 holds. Under #256's re-detect
        # model this was 3 (every flat bar re-asked, because nothing ever filled) -- so this
        # number is also what distinguishes the two fill models.
        assert rule.detect_calls == 1


class TestPendingLifespanInvariant:
    """A pending setup must never survive more than one bar (#254 / #257).

    HONEST NOTE ON COVERAGE: this invariant cannot be violated through the public API, because
    since #257 the fill is unconditional -- there is no input that makes the engine carry a setup.
    So there is no red-then-green test for it, and pretending otherwise would be theatre.

    What actually protects it is that the assertion sits in the hot loop of `backtest()`, so
    EVERY existing test in this file, the baseline golden, and the whole sim suite now execute it.
    If someone reintroduces a carried pending -- resting orders for #260's Option B, say -- the
    suite fails loudly with a message naming the rule and the bar, instead of the silence that let
    #254 survive the entire project.

    The tests below pin the contract itself so its value is stated somewhere a reader will find.
    """

    def test_a_long_series_that_rarely_fires_never_trips_the_invariant(self) -> None:
        """The #254 shape: a rule that mostly declines, over many bars, must run clean."""
        rule = _ScriptedRule(
            trigger_ts=600, entry=Decimal(110), stop=Decimal(95), target=Decimal(130)
        )
        candles = [_candle(ts, "100", "101", "99", "100") for ts in range(0, 6000, 60)]
        result = backtest(rule, candles)  # must not raise
        assert isinstance(result, BacktestResult)

    def test_an_unfillable_setup_still_resolves_within_one_bar(self) -> None:
        """The exact #254 trigger: entry and stop both unreachable, forever.

        Before #257 this pinned `pending` for the rest of the series. It must now fill on the very
        next bar instead, which is what keeps the lifespan at 1.
        """
        rule = _StaleThenReachableRule(switch_ts=10**9)
        candles = [_candle(ts, "100", "101", "99", "100") for ts in range(0, 3000, 60)]
        result = backtest(rule, candles)  # must not raise

        assert len(result.trades) == 1  # filled immediately, then held to the end of the series
        assert result.trades[0].outcome == "open"
