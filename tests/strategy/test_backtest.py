"""Tests for keel.strategy.backtest: the historical backtest engine.

Drives the backtester with test-only `Rule` subclasses (per the Phase 2 plan, this
module must not depend on any concrete rule implementation) against small,
hand-built candle series where the expected outcome is known exactly.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from keel_core.config import FeesConfig

from keel.compliance.screen import ScreenPolicy
from keel.strategy.backtest import (
    SLIPPAGE_CAP_PCT,
    SLIPPAGE_FLOOR_PCT,
    SLIPPAGE_REFERENCE_QUOTE_VOLUME,
    SLIPPAGE_TAIL_PRODUCT,
    SLIPPAGE_TAIL_QUOTE_VOLUME,
    TAKER_FEE_PCT,
    BacktestResult,
    backtest,
    slippage_for_quote_volume,
)
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

    def __init__(
        self,
        trigger_ts: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
        product_id: str = "BTC-USD",
    ) -> None:
        self.trigger_ts = trigger_ts
        self.entry = entry
        self.stop = stop
        self.target = target
        self.product_id = product_id

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        window = next(iter(candles_by_tf.values()))
        latest = window[-1]
        if latest.ts != self.trigger_ts:
            return None
        return Setup(
            product_id=self.product_id,
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
        # Entry far above and stop far BELOW every price in the series, so neither is ever
        # touched. The stop has to sit below the market: since the #442 gap fix, a bar whose
        # HIGH is below a long's stop counts as a touch (the level was passed wholesale), so
        # the old fixture's stop at 900 over a ~100 market would exit at once -- a gap
        # through the stop, not an untouched one.
        entry, stop, target = (Decimal(1000), Decimal(50), Decimal(1100))
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


# -- #259: per-product slippage scaled from liquidity ------------------------------------------
#
# Median daily quote volumes below are MEASURED, from this repo's own corpus on 2026-08-16
# (`median_daily_quote_volume` over the cached ONE_DAY bars: BTC $571,268,510, ETH $337,185,168,
# SOL $108,953,971, WLD $1,245,626, TON $369,944) -- three-plus orders of magnitude from top to
# tail. They are why one global 5bp could not stand: since #257 that constant is the ONLY term
# modelling spread crossing and market impact, and the error ran in the flattering direction on
# exactly the thin assets that kept showing up as outliers (TON gross PF 3.751 on n=9; WLD 2.810
# on n=12).


class TestLiquidityScaledSlippage:
    """The mapping `slippage_for_quote_volume`: monotone, bounded, and owned as an assumption.

    These are pure unit tests over a synthetic volume ladder -- no candles, no rule -- pinning
    the four properties the mapping is required to have (#259): the anchor maps to the floor, a
    materially thinner product pays materially more, the cap clamps, and the whole ladder is
    monotone (more liquid -> never more slippage).
    """

    def test_the_anchor_volume_maps_to_exactly_the_floor(self) -> None:
        """BTC-class liquidity stays at 5bp: the liquid end of the mapping IS the old constant."""
        assert slippage_for_quote_volume(SLIPPAGE_REFERENCE_QUOTE_VOLUME) == SLIPPAGE_FLOOR_PCT

    def test_a_product_100x_thinner_pays_10x_the_slippage(self) -> None:
        """sqrt scaling: 100x thinner -> sqrt(100) = 10x the rate, i.e. 50bp.

        Pinned as a NUMBER, not an inequality, so a change to the curve's shape is named here.
        Until #523 this point was ALSO the cap -- #259 chose 50bp so the clamp bound at exactly
        100x below the anchor -- and $5M/day is now an ordinary interior point of the sqrt
        region, which is the correction: the band between the $1M admission floor and here is
        the cohort that used to be flattened onto one rate.
        """
        hundred_x_thinner = SLIPPAGE_REFERENCE_QUOTE_VOLUME / Decimal(100)

        assert slippage_for_quote_volume(hundred_x_thinner) == Decimal("0.005")
        assert slippage_for_quote_volume(hundred_x_thinner) == SLIPPAGE_FLOOR_PCT * Decimal(10)
        assert slippage_for_quote_volume(hundred_x_thinner) < SLIPPAGE_CAP_PCT

    def test_the_cap_clamps_only_below_the_corpus_tail(self) -> None:
        """Something an order of magnitude thinner than the corpus tail clamps; the tail does not.

        Since #523 the cap is the curve read AT the tail, so the clamp is a bound for assets
        nobody has measured -- not a rate the measured corpus is charged. A hypothetical
        $37K/day book would demand ~581bp unclamped, far outside anything the sqrt prior was
        fitted near; that is the extrapolation the bound exists to refuse.
        """
        far_below_the_tail = SLIPPAGE_TAIL_QUOTE_VOLUME / Decimal(10)
        unclamped = SLIPPAGE_FLOOR_PCT * (
            SLIPPAGE_REFERENCE_QUOTE_VOLUME / far_below_the_tail
        ).sqrt()

        assert unclamped > SLIPPAGE_CAP_PCT  # the clamp is genuinely load-bearing here
        assert slippage_for_quote_volume(far_below_the_tail) == SLIPPAGE_CAP_PCT

    def test_more_liquid_than_the_anchor_never_pays_less_than_the_floor(self) -> None:
        """The floor is a bound, not just a starting point: BTC itself sits just above the anchor
        (measured $571M vs $500M) and must still pay the full 5bp, never a discount below it."""
        assert slippage_for_quote_volume(SLIPPAGE_REFERENCE_QUOTE_VOLUME * Decimal(4)) == (
            SLIPPAGE_FLOOR_PCT
        )
        assert slippage_for_quote_volume(Decimal("571268510")) == SLIPPAGE_FLOOR_PCT

    def test_monotone_across_a_volume_ladder(self) -> None:
        """Thinner -> same or worse, all the way down; strictly worse through the unclamped middle.

        The ladder spans more than the corpus's own range (BTC ~$571M down to ~$100K) so the
        property is pinned beyond today's data, not just on it.
        """
        ladder = [
            Decimal(v)
            for v in (
                "2000000000",  # more liquid than the anchor -> floor
                "1000000000",
                "500000000",  # the anchor -> floor
                "100000000",  # SOL-class -> unclamped
                "50000000",
                "10000000",
                "5000000",  # exactly 100x thinner -> the cap
                "1000000",  # the admission floor -> clamped
                "100000",
            )
        ]
        rates = [slippage_for_quote_volume(v) for v in ladder]

        assert all(a <= b for a, b in zip(rates, rates[1:], strict=False))
        # Strictly increasing wherever neither end's clamp is active (anchor -> 100x-thinner).
        assert rates[2] < rates[3] < rates[4] < rates[5] < rates[6]

    def test_zero_volume_is_the_cap_not_an_error(self) -> None:
        """`median_daily_quote_volume` returns 0 for empty input; the mapping must stay total and
        treat 'no evidence of liquidity' as maximally thin (the cap), the fail-closed direction.
        A NEGATIVE statistic (corrupt data, not merely absent) hits the same `<= 0` guard."""
        assert slippage_for_quote_volume(Decimal(0)) == SLIPPAGE_CAP_PCT
        assert slippage_for_quote_volume(Decimal("-1")) == SLIPPAGE_CAP_PCT

    def test_measured_corpus_medians_map_where_the_corpus_says_they_should(self) -> None:
        """BTC at the floor, ETH a hair above it (~6.1bp), TON at the cap -- the measured anchors
        that motivated #259, restated as the mapping's own outputs so the model can be audited
        against the data that produced it.

        TON is still the row that reaches the cap, but since #523 it does so by ARRIVING there
        along the curve rather than by being clamped: its rounded 2026-08-16 median sits a
        hair below the exact 2026-08-30 tail the cap is derived from, so the clamp shaves
        0.0176bp off it. Either way it is 183.8bp -- the tail is charged what the model says
        the tail costs.
        """
        btc = slippage_for_quote_volume(Decimal("571268510"))
        eth = slippage_for_quote_volume(Decimal("337185168"))
        ton = slippage_for_quote_volume(Decimal("369944"))

        assert btc == Decimal("0.0005")
        assert Decimal("0.0005") < eth < Decimal("0.001")  # ~6.1bp
        assert ton == SLIPPAGE_CAP_PCT == Decimal("0.01838")


#: The pre-#523 cap: 50bp, the round number chosen so the clamp bound at exactly 100x below the
#: anchor. Kept here as a LITERAL on purpose. The two classes below measure the distance
#: travelled from it, and a distance that silently follows the constant it is measured against
#: measures nothing.
OLD_SLIPPAGE_CAP_PCT = Decimal("0.005")


class TestTheCapIsDerivedFromTheCorpusTail:
    """#523: the cap is DERIVED from the thinnest measured product, and the derivation is
    checkable here instead of being asserted in prose.

    The corpus these numbers are measured over lives in a deployment database, not in this
    repository, so no test can recompute the tail volume from live data -- re-deriving it is a
    documented one-liner against a deployment root (see `SLIPPAGE_TAIL_QUOTE_VOLUME`'s own
    comment in `strategy/backtest.py`). What a test CAN do, and what these do, is refuse to let
    the four numbers drift apart: the cap must remain this module's own curve evaluated at the
    tail volume, so editing any ONE of floor, anchor, tail volume or cap fails here.

    `assert SLIPPAGE_CAP_PCT == Decimal("0.01838")` would pin nothing at all -- it restates a
    literal already in the file, and it is exactly the shape of assertion that let a round
    number sit unexamined for two issues.

    What this deliberately does NOT catch: a change to `SLIPPAGE_TAIL_QUOTE_VOLUME` too small to
    move the cap at reporting precision (dropping its trailing decimals, say). That is the
    correct tolerance rather than a gap -- the cap is quantised to the tenth of a basis point,
    so a tail volume that rounds to the same cap IS the same cap. A drift large enough to matter
    fails: $369,944 -> $340,000 is caught at `0.01838 != 0.01917`.
    """

    def _tail_rate(self) -> Decimal:
        """The unclamped curve at the corpus tail, computed from the constants -- the same
        expression `slippage_for_quote_volume` evaluates, written out so the arithmetic is
        visible rather than borrowed from the function under test."""
        return SLIPPAGE_FLOOR_PCT * (
            SLIPPAGE_REFERENCE_QUOTE_VOLUME / SLIPPAGE_TAIL_QUOTE_VOLUME
        ).sqrt()

    def test_the_cap_is_the_curve_read_at_the_tail(self) -> None:
        """floor x sqrt(anchor / tail volume), quantised to the tenth of a basis point every
        caller reports at. This is the whole claim the constant makes about itself."""
        assert SLIPPAGE_CAP_PCT == self._tail_rate().quantize(
            Decimal("0.00001"), rounding=ROUND_HALF_UP
        )

    def test_the_quantisation_is_invisible_to_every_consumer(self) -> None:
        """Rounding 183.8175bp to 183.8bp discards 0.0176bp, which no caller can print: both
        `commands/simulate.render_slippage_assumptions` and `sim/report._render_slippage_rows`
        format at one decimal place. A derivation whose rounding CHANGED the reported number
        would be a fifth number in disguise."""
        discarded = self._tail_rate() - SLIPPAGE_CAP_PCT

        assert abs(discarded) <= Decimal("0.000005")  # at most half a tenth of a bp
        assert f"{SLIPPAGE_CAP_PCT * 10000:.1f}bp" == f"{self._tail_rate() * 10000:.1f}bp"
        assert f"{SLIPPAGE_CAP_PCT * 10000:.1f}bp" == "183.8bp"

    def test_the_tail_product_is_named_beside_its_volume(self) -> None:
        """A volume without the product it was measured on is unrecoverable: nobody can re-run
        the one-liner. The constant that says WHICH asset is part of the derivation."""
        assert SLIPPAGE_TAIL_PRODUCT == "TON-USD"
        assert slippage_for_quote_volume(SLIPPAGE_TAIL_QUOTE_VOLUME) == SLIPPAGE_CAP_PCT

    def test_the_clamp_now_binds_beneath_the_admission_floor(self) -> None:
        """The defect #523 names, pinned from the other side.

        The clamp binds at `anchor x (floor / cap)^2`. Under the old 50bp cap that was
        $5,000,000/day while `ScreenPolicy.min_median_daily_volume` admits from $1,000,000/day,
        so the entire $1M-$5M band was simultaneously admissible and capped -- one identical
        rate across a 4.3x spread in liquidity, the flat-fee model #259 shipped to remove. At
        the tail-derived cap the binding point is ~$370K/day, BELOW the admission floor, so no
        admissible product can be clamped: every one of them is priced by its own liquidity.
        """
        admission_floor = ScreenPolicy().min_median_daily_volume

        def binds_below(cap: Decimal) -> Decimal:
            return SLIPPAGE_REFERENCE_QUOTE_VOLUME * (SLIPPAGE_FLOOR_PCT / cap) ** 2

        assert binds_below(OLD_SLIPPAGE_CAP_PCT) > admission_floor  # the defect
        assert binds_below(SLIPPAGE_CAP_PCT) < admission_floor  # the correction
        # and the thinnest ADMISSIBLE product is strictly inside the sqrt region
        assert SLIPPAGE_FLOOR_PCT < slippage_for_quote_volume(admission_floor) < SLIPPAGE_CAP_PCT

    def test_the_admission_floor_itself_did_not_move(self) -> None:
        """#523 option 3 -- raising the admission floor to $5M/day -- was measured and rejected:
        it changes no rate for any asset it keeps and disqualifies live PAXG at $1.25M median.
        This change is option 1 only. If the floor ever moves, that is its own decision."""
        assert ScreenPolicy().min_median_daily_volume == Decimal("1000000")


class TestControlsAboveTheCapThresholdAreUnaffected:
    """#523 acceptance criterion 3: an asset above the cap threshold (BTC/ETH) is unchanged by
    the cap moving -- proved by running the engine at both caps, not by reading the clamp.

    The controls are the load-bearing half of the file: without a cohort asset that DOES move,
    every "identical" assertion here would be satisfied by a harness incapable of showing a
    difference, so `test_the_capped_cohort_is_the_half_that_moves` runs the same comparison on
    CRO and demands the opposite answer.

    Medians are the ones measured on 2026-08-30 in
    `docs/experiments/2026-08-30-slippage-cap-options.md` (a dated record, quoted, not edited).
    """

    _BTC = Decimal("568492017.0755083632")
    _ETH = Decimal("330302563.9767160125")
    _CRO = Decimal("1139342.39804")

    @staticmethod
    def _rate_under_cap(volume: Decimal, cap: Decimal) -> Decimal:
        """`slippage_for_quote_volume` with the clamp's thin end supplied by the caller.

        Restated rather than monkeypatched, so the module under test stays the module that
        ships and "what the OLD cap would have charged" is a value this test computes rather
        than a global it mutates. `test_the_harness_reproduces_the_shipped_mapping` keeps the
        restatement honest.
        """
        if volume <= 0:
            return cap
        unclamped = SLIPPAGE_FLOOR_PCT * (SLIPPAGE_REFERENCE_QUOTE_VOLUME / volume).sqrt()
        return min(max(unclamped, SLIPPAGE_FLOOR_PCT), cap)

    def _candles(self) -> list[Candle]:
        # The TestPerProductSlippageInBacktest shape: trigger at ts=60, fill at ts=120's open,
        # exit at the target. Liquidity plays no part in the candles, so any difference between
        # two runs is the cost model's and nothing else.
        return [
            _candle(0, "100", "101", "99", "100"),
            _candle(60, "104", "106", "103", "105"),
            _candle(120, "105", "112", "104", "108"),
            _candle(180, "111", "135", "109", "132"),
        ]

    def _run_at(self, product_id: str, volume: Decimal, cap: Decimal) -> BacktestResult:
        rule = _ScriptedRule(
            60, Decimal(110), Decimal(95), Decimal(130), product_id=product_id
        )
        rate = self._rate_under_cap(volume, cap)
        return backtest(rule, self._candles(), slippage_by_product=lambda pid: rate)

    def test_the_harness_reproduces_the_shipped_mapping(self) -> None:
        """The control on the control: at the SHIPPED cap this test's clamp must agree with the
        shipped function everywhere, or every comparison below is measuring the harness."""
        for volume in (self._BTC, self._ETH, self._CRO, SLIPPAGE_TAIL_QUOTE_VOLUME, Decimal(0)):
            assert self._rate_under_cap(volume, SLIPPAGE_CAP_PCT) == slippage_for_quote_volume(
                volume
            )

    def test_control_rates_are_identical_under_both_caps(self) -> None:
        """BTC 5.0bp, ETH ~6.1bp: both sit in the sqrt region under either cap, so the clamp
        never touched them and moving it cannot."""
        for volume in (self._BTC, self._ETH):
            old = self._rate_under_cap(volume, OLD_SLIPPAGE_CAP_PCT)
            new = self._rate_under_cap(volume, SLIPPAGE_CAP_PCT)

            assert old == new == slippage_for_quote_volume(volume)
            assert new < OLD_SLIPPAGE_CAP_PCT

        # Pinned ABSOLUTELY as well as relatively. "Identical under both caps" is satisfied by
        # any change that moves both sides together -- re-anchoring the curve instead of
        # re-capping it, say, which is a plausible way to implement #523 and would silently
        # move ETH. These are the rates the 2026-08-30 run measured for the controls.
        assert slippage_for_quote_volume(self._BTC) == SLIPPAGE_FLOOR_PCT  # 5.00bp
        assert f"{slippage_for_quote_volume(self._ETH) * 10000:.2f}" == "6.15"

    def test_control_backtests_are_bit_identical_under_both_caps(self) -> None:
        """The regression the acceptance criterion asks for, run through the engine: same rule,
        same candles, same liquidity -- only the cap differs -- and every stat, every fill price
        and every P&L comes back equal."""
        for product_id, volume in (("BTC-USD", self._BTC), ("ETH-USD", self._ETH)):
            old = self._run_at(product_id, volume, OLD_SLIPPAGE_CAP_PCT)
            new = self._run_at(product_id, volume, SLIPPAGE_CAP_PCT)

            assert new == old
            assert new.trades[0].entry == old.trades[0].entry
            assert new.trades[0].exit == old.trades[0].exit
            assert new.trades[0].pnl == old.trades[0].pnl
            assert new.profit_factor == old.profit_factor

    def test_the_capped_cohort_is_the_half_that_moves(self) -> None:
        """CRO, $1.14M/day, the thinnest admissible product in the cohort: capped at 50.0bp and
        priced at 104.7bp by the model's own curve, a 2.09x understatement. It must move, and
        it must move in the CONSERVATIVE direction -- worse entry, worse exit, less P&L. If this
        passed alongside the controls only because nothing here can differ, this is the test
        that fails."""
        old = self._run_at("CRO-USD", self._CRO, OLD_SLIPPAGE_CAP_PCT)
        new = self._run_at("CRO-USD", self._CRO, SLIPPAGE_CAP_PCT)

        assert self._rate_under_cap(self._CRO, OLD_SLIPPAGE_CAP_PCT) == OLD_SLIPPAGE_CAP_PCT
        assert self._rate_under_cap(self._CRO, SLIPPAGE_CAP_PCT) > OLD_SLIPPAGE_CAP_PCT
        assert new != old
        assert new.trades[0].entry > old.trades[0].entry
        assert new.trades[0].exit < old.trades[0].exit
        assert new.trades[0].pnl is not None and old.trades[0].pnl is not None
        assert new.trades[0].pnl < old.trades[0].pnl


class TestPerProductSlippageInBacktest:
    """`backtest(slippage_by_product=...)` prices each product's fills from its own liquidity.

    The contract has two halves, and both are tested: a run that passes the resolver gets
    per-product costs, and a run that does not keeps the flat 5bp exactly as before -- the
    parameter overrides, it never re-defaults (#259's requirement that every existing caller's
    behaviour stay identical until it opts in).
    """

    _VOLUMES = {
        "BTC-USD": Decimal("571268510"),  # measured corpus medians, 2026-08-16
        "TON-USD": Decimal("369944"),
    }

    def _candles(self) -> list[Candle]:
        # The TestKnownWinningTrade shape: trigger at ts=60, fill at ts=120's open (105),
        # exit at the target (130). Liquidity plays no role in the candles -- only in the
        # slippage the resolver returns -- so any difference between runs is the cost model's.
        return [
            _candle(0, "100", "101", "99", "100"),
            _candle(60, "104", "106", "103", "105"),
            _candle(120, "105", "112", "104", "108"),
            _candle(180, "111", "135", "109", "132"),
        ]

    def _rule(self, product_id: str = "BTC-USD") -> _ScriptedRule:
        return _ScriptedRule(60, Decimal(110), Decimal(95), Decimal(130), product_id=product_id)

    def _resolver(self, product_id: str) -> Decimal:
        return slippage_for_quote_volume(self._VOLUMES[product_id])

    def test_the_resolver_overrides_the_flat_constant_on_the_entry_fill(self) -> None:
        result = backtest(
            self._rule(), self._candles(), slippage_by_product=lambda pid: Decimal("0.005")
        )

        assert result.trades[0].entry == Decimal(105) * Decimal("1.005")

    def test_a_thin_product_nets_strictly_less_than_a_liquid_one(self) -> None:
        """Two products, same candles, same setup; only the liquidity statistic differs.

        TON pays more to enter AND receives less on the exit -- both legs are worse, which is
        the direction check #259 demands: the correction is conservative on thin assets, never
        favourable.
        """
        btc = backtest(self._rule("BTC-USD"), self._candles(), slippage_by_product=self._resolver)
        ton = backtest(self._rule("TON-USD"), self._candles(), slippage_by_product=self._resolver)

        assert ton.trades[0].entry > btc.trades[0].entry
        assert ton.trades[0].exit < btc.trades[0].exit
        assert ton.trades[0].pnl is not None and btc.trades[0].pnl is not None
        assert ton.trades[0].pnl < btc.trades[0].pnl

    def test_a_resolver_returning_the_floor_is_indistinguishable_from_no_resolver(self) -> None:
        """The fallback contract: a product with no liquidity statistic behaves EXACTLY as the
        flat constant -- the caller's resolver answers 'flat rate' and the engine must not be
        able to tell that apart from the default."""
        via_resolver = backtest(
            self._rule(), self._candles(), slippage_by_product=lambda pid: SLIPPAGE_FLOOR_PCT
        )
        flat = backtest(self._rule(), self._candles())

        assert via_resolver.trades[0].pnl == flat.trades[0].pnl
        assert via_resolver.trades[0].entry == flat.trades[0].entry

    def test_without_the_resolver_everything_prices_at_the_flat_default(self) -> None:
        """The existing-caller guarantee, pinned: omitting the parameter must reproduce a run
        that passes the flat rate explicitly, trade for trade."""
        default_run = backtest(self._rule(), self._candles())
        explicit_run = backtest(self._rule(), self._candles(), slippage_pct=Decimal("0.0005"))

        assert default_run.trades[0].pnl == explicit_run.trades[0].pnl
        assert default_run.trades[0].entry == Decimal(105) * Decimal("1.0005")


# ---------------------------------------------------------------------------
# #442: the ratchet-only exit policy (trailing stop / break-even roll)
# ---------------------------------------------------------------------------


class _ScriptedParamRule(_ScriptedRule):
    """`_ScriptedRule` carrying a `params` dict -- the per-family exit-policy knobs
    (`trail_atr_mult` / `be_roll_rr` / `atr_period`) `strategy.exit_policy.policy_for`
    reads off the rule. `detect`/`exit_signal` behave exactly as the parent's."""

    def __init__(
        self,
        trigger_ts: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
        params: dict,
        product_id: str = "BTC-USD",
    ) -> None:
        super().__init__(trigger_ts, entry, stop, target, product_id=product_id)
        self.params = dict(params)


def _flat_then_run_bars(trigger_ts: int, after: list[tuple[str, str, str, str]]) -> list[Candle]:
    """Warmup bars flat at 100 (true range exactly 2 on every bar, so ATR(14) == 2
    throughout), the trigger bar, then the caller's `(o, h, l, c)` tuples -- each rising
    bar's true range is also 2 by construction where the caller keeps `l == prev c`,
    `h == l + 2`."""
    bars = [_candle(i * 3600, "100", "101", "99", "100") for i in range(trigger_ts // 3600 + 1)]
    for i, (o, h, low, c) in enumerate(after, len(bars)):
        bars.append(_candle(i * 3600, o, h, low, c))
    return bars


def test_trailing_exits_earlier_and_higher_than_the_static_stop() -> None:
    """The #442 wiring, behaviorally: a 2xATR trail on a rising-then-retracing series
    ratchets the stop up bar by bar and exits on the retrace at a level the STATIC stop
    never reaches -- same series, same setup, only `trail_atr_mult` differs.

    Bars (fee 0, slippage 0, ATR == 2 on every bar):
      fill bar:  o100 h101 l99  c100  -> trail 100-4 = 96 (initial stop 94)
      rising:    o100 h102 l100 c102  -> trail 102-4 = 98
      rising:    o102 h104 l102 c104  -> trail 104-4 = 100
      retrace:   o102 h103 l97  c98   -> low 97 touches the TRAILED stop 100 -> exit 100
      (static arm: low 97 misses the static 94; the NEXT bar's low 93 exits at 94)
    """
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),  # fill bar
        ("100", "102", "100", "102"),
        ("102", "104", "102", "104"),
        ("102", "103", "97", "98"),  # retrace: trailing arm exits here
        ("98", "99", "93", "94"),  # static arm exits here
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    trail_rule = _ScriptedParamRule(
        trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {"trail_atr_mult": Decimal("2")}
    )
    static_rule = _ScriptedParamRule(trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {})

    trailed = backtest(trail_rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))
    static = backtest(static_rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert trailed.n_trades == 1
    assert static.n_trades == 1
    assert trailed.trades[0].exit == Decimal("100")  # the RATCHETED stop, not the static 94
    assert trailed.trades[0].exit_ts == (trigger_ts + 4 * 3600)
    assert static.trades[0].exit == Decimal("94")
    assert static.trades[0].exit_ts == (trigger_ts + 5 * 3600)
    assert trailed.trades[0].pnl is not None and static.trades[0].pnl is not None
    assert trailed.trades[0].pnl > static.trades[0].pnl


def test_break_even_roll_exits_at_entry_after_the_threshold_clears() -> None:
    """`be_roll_rr=1`: once the bar's high clears entry + 1x the ORIGINAL risk (110 vs
    risk 10), the stop rolls to the entry and the very next dip to 99 exits there -- a
    scratch instead of the static arm's full ride down to the 90 stop."""
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),  # fill bar: high 101 misses the 110 threshold
        ("100", "111", "100", "108"),  # high 111 clears +1R -> stop rolls to entry 100
        ("106", "107", "99", "100"),  # low 99 touches the rolled stop -> exit at 100
        ("100", "101", "89", "90"),  # static arm: low 89 touches the 90 stop
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    be_rule = _ScriptedParamRule(
        trigger_ts, Decimal("100"), Decimal("90"), Decimal("130"), {"be_roll_rr": Decimal("1")}
    )
    static_rule = _ScriptedParamRule(trigger_ts, Decimal("100"), Decimal("90"), Decimal("130"), {})

    rolled = backtest(be_rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))
    static = backtest(static_rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert rolled.n_trades == 1
    assert static.n_trades == 1
    assert rolled.trades[0].exit == Decimal("100")
    assert rolled.trades[0].exit_ts == (trigger_ts + 3 * 3600)
    assert rolled.trades[0].pnl == Decimal("0")  # a scratch at the rolled stop
    assert static.trades[0].exit == Decimal("90")
    assert static.trades[0].exit_ts == (trigger_ts + 4 * 3600)


def test_a_rule_without_exit_params_is_byte_identical_to_explicit_off() -> None:
    """The default-OFF guarantee: `params={}` (every existing rule row) and an explicit
    `{"trail_atr_mult": None, "be_roll_rr": None}` produce identical trades -- the wiring
    cannot change any existing rule's backtest until an operator turns a knob on."""
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),
        ("100", "102", "100", "102"),
        ("102", "104", "102", "104"),
        ("102", "103", "97", "98"),
        ("98", "99", "93", "94"),
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    unset = _ScriptedParamRule(trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {})
    explicit_off = _ScriptedParamRule(
        trigger_ts,
        Decimal("100"),
        Decimal("94"),
        Decimal("130"),
        {"trail_atr_mult": None, "be_roll_rr": None},
    )

    a = backtest(unset, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))
    b = backtest(explicit_off, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert [(t.entry, t.exit, t.exit_ts, t.pnl) for t in a.trades] == [
        (t.entry, t.exit, t.exit_ts, t.pnl) for t in b.trades
    ]


# ---------------------------------------------------------------------------
# #442 review: a bar that gaps ENTIRELY through the stop still exits
# ---------------------------------------------------------------------------


def test_a_static_stop_gapped_through_by_a_whole_bar_exits_at_that_bars_open() -> None:
    """The containment-only touch check (`low <= stop <= high`) let a bar whose HIGH sits
    below the stop slip past it silently: pre-fix, the gap bar triggered nothing, and the
    recovery bar's in-range touch then exited at the STOP -- a better price than the market
    ever offered after the gap. The gap bar must exit, at its OPEN (worse than the stop --
    the honest fill for a level passed wholesale).

      fill bar:  o100 h101 l99  c100  -> stop 94 clear
      holding:   o99  h100 l98  c99   -> no touch
      GAP:       o90  h92  l88  c89   -> high 92 < stop 94 -> exit at the open 90
      recovery:  o89  h95  l88  c93   -> pre-fix exited HERE, flattered, at 94
    """
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),
        ("99", "100", "98", "99"),
        ("90", "92", "88", "89"),  # the gap through the stop
        ("89", "95", "88", "93"),  # pre-fix: the flattered in-range exit at 94
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    rule = _ScriptedParamRule(trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {})

    result = backtest(rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert result.n_trades == 1
    assert result.trades[0].exit == Decimal("90")  # the gap bar's OPEN, not the 94 stop
    assert result.trades[0].exit_ts == trigger_ts + 3 * 3600
    assert result.trades[0].pnl == Decimal("-10")  # 100 -> 90, fee 0, slippage 0
    assert result.trades[0].outcome == "loss"


def test_an_open_above_the_stop_with_a_piercing_low_still_fills_at_the_stop() -> None:
    """The case the fix must NOT change: the bar OPENS above the stop and its low pierces
    it -- the level traded, so the fill is the stop itself, exactly as before."""
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),  # fill bar
        ("105", "106", "92", "95"),  # open 105 > stop 94, low 92 pierces -> exit at 94
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    rule = _ScriptedParamRule(trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {})

    result = backtest(rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert result.n_trades == 1
    assert result.trades[0].exit == Decimal("94")
    assert result.trades[0].exit_ts == trigger_ts + 2 * 3600


def test_a_trailing_stop_stranded_by_a_gap_down_bar_exits_instead() -> None:
    """The ratchet makes the gap hole bite hardest: a trail sitting near price is passed
    wholesale by one gap-down bar, and the ratchet then REFUSES to lower the stop to
    re-reach it -- pre-fix the position stranded open while every later bar's high stayed
    below the stranded level, understating trailing losses. With the fix it exits on the
    gap bar itself, at its open.

      fill bar:  o100 h101 l99  c100  -> trail 96 (ATR 2)
      rising:    o100 h102 l100 c102  -> trail 98
      rising:    o102 h104 l102 c104  -> trail 100
      GAP:       o97  h98  l95  c96   -> high 98 < trailed 100 -> exit at the open 97
      stranded:  o96  h97  l94  c95   -> pre-fix: rides here and on, stop pinned at 100
    """
    trigger_ts = 14 * 3600
    after = [
        ("100", "101", "99", "100"),
        ("100", "102", "100", "102"),
        ("102", "104", "102", "104"),
        ("97", "98", "95", "96"),  # the gap through the trailed stop
        ("96", "97", "94", "95"),  # pre-fix: stranded, stop ratcheted at 100 forever
    ]
    candles = _flat_then_run_bars(trigger_ts, after)
    trail_rule = _ScriptedParamRule(
        trigger_ts, Decimal("100"), Decimal("94"), Decimal("130"), {"trail_atr_mult": Decimal("2")}
    )

    result = backtest(trail_rule, candles, fee_pct=Decimal(0), slippage_pct=Decimal(0))

    assert result.n_trades == 1
    assert result.trades[0].exit == Decimal("97")  # the gap bar's OPEN
    assert result.trades[0].exit_ts == trigger_ts + 4 * 3600
    assert result.trades[0].outcome == "loss"
