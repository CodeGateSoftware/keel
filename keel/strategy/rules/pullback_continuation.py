"""Rule: parameterized pullback-continuation family.

Unifies source-02 §2.0/§2.1 ("Daily Chore": EMA(8/20/50) fan, touch of EMA8, 30%-body pin
bar, 1:1 measured-move target) and source-07 §7.1 (EMA(20/50/200), dip into the 20-50 EMA
band, previous-swing-high target, Fib 1.272/1.618 extension target) into ONE parameterized
rule. The *structure* — Identify -> Predict -> Decide -> Execute (source-02 §2.0) — is
fixed; the *parameters* (`ema_periods`, `entry_zone`, `signal_patterns`, `buffer_ticks`,
`stop_method`, `target_method`) are the tunable knobs the backtester optimizes (spec §8
Rule 1).

Long-only spot: `detect()` only ever returns a **long** `Setup` on the bullish setup.
The bearish mirror (EMA fan inverted, pullback into the zone from below, a bearish signal
candle) never shorts — it is wired to `exit_signal()` only (close a held long / don't-buy
filter), per spec §8 and the Global Constraint "no shorts, ever".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from keel.analysis import candles as candle_lib
from keel.analysis import indicators, levels, regime
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

EntryZone = Literal["ema_touch", "ema_band"]
StopMethod = Literal["fixed", "atr"]
TargetMethod = Literal["measured_1to1", "swing", "fib_ext"]

#: The pattern names `_match_signal_pattern` below can actually fire on -- declared as a
#: `Literal` for the same reason `EntryZone`/`StopMethod`/`TargetMethod` are: it is this rule's
#: own statement of what it accepts, in the one place that also implements it. A name outside
#: this set is not an error the rule can raise (the matcher simply never matches it and the rule
#: never signals, forever), so a caller taking operator-typed patterns -- `keel rules add` --
#: reads the set off this annotation rather than keeping a second copy that would drift.
SignalPattern = Literal[
    "pin_bar",
    "hammer",
    "shooting_star",
    "doji",
    "marubozu",
    "tweezer",
    "three_bar_reversal",
]

_DEFAULT_EMA_PERIODS: tuple[int, ...] = (8, 20, 50)
_DEFAULT_SIGNAL_PATTERNS: tuple[SignalPattern, ...] = ("pin_bar",)
_ATR_PERIOD = 14
# source-07 §7.4: Fib 1.272/1.618 extension is a third target-method option; 1.272 (the
# nearer, more conservative extension) is the default ratio used here.
_FIB_EXTENSION_RATIO = "1.272"


class PullbackContinuation(Rule):
    """Parameterized pullback-continuation family (long entries; bearish mirror exits).

    One rule instance trades a single `product_id` off a single trading-timeframe
    `granularity` key of `candles_by_tf` (multi-timeframe bias/trigger gating is the
    evaluation engine's job, spec §8 Task 7 — out of scope here).
    """

    def __init__(
        self,
        product_id: str,
        granularity: Granularity = Granularity.ONE_HOUR,
        ema_periods: tuple[int, ...] = _DEFAULT_EMA_PERIODS,
        entry_zone: EntryZone = "ema_touch",
        signal_patterns: tuple[SignalPattern, ...] = _DEFAULT_SIGNAL_PATTERNS,
        buffer_ticks: Decimal = Decimal("0.02"),
        stop_method: StopMethod = "fixed",
        target_method: TargetMethod = "measured_1to1",
        name: str = "pullback_continuation",
    ) -> None:
        self.name = name
        self.product_id = product_id
        self.granularity = granularity
        self.params: dict = {
            "ema_periods": ema_periods,
            "entry_zone": entry_zone,
            "signal_patterns": signal_patterns,
            "buffer_ticks": buffer_ticks,
            "stop_method": stop_method,
            "target_method": target_method,
        }

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Identify -> Predict -> Decide -> Execute (source-02 §2.0), bullish side only."""
        candles = candles_by_tf.get(self.granularity, [])
        if len(candles) < 2:
            return None

        # Identify: bullish condition + phase-two pullback.
        if regime.detect_condition(candles) != regime.Condition.BULLISH:
            return None
        if regime.detect_phase(candles) != regime.Phase.PULLBACK:
            return None

        # Predict: EMA fan confirms trend likely to continue.
        fan = indicators.ema_fan(candles, periods=self.params["ema_periods"])
        last_idx = len(candles) - 1
        if not indicators.fan_aligned(fan, last_idx, "bullish"):
            return None

        signal_candle = candles[-1]
        if not self._in_entry_zone(signal_candle, fan, last_idx, "bullish"):
            return None

        # Decide: a qualifying signal candle gives the trigger.
        pattern = self._match_signal_pattern(candles, "bullish")
        if pattern is None:
            return None

        # Execute: buy-stop above the signal high, stop/target per the configured methods.
        entry = signal_candle.high + self.params["buffer_ticks"]
        stop = self._compute_stop(candles, signal_candle)
        if stop is None or stop >= entry:
            return None

        target = self._compute_target(candles, entry, stop)
        if target is None or target <= entry:
            return None

        context = {
            "condition": regime.Condition.BULLISH.value,
            "phase": regime.Phase.PULLBACK.value,
            "fan_aligned": True,
            "entry_zone": self.params["entry_zone"],
            "pattern": pattern,
            "stop_method": self.params["stop_method"],
            "target_method": self.params["target_method"],
        }
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context=context,
            ts=signal_candle.ts,
        )

    def exit_signal(
        self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> bool:
        """The bearish mirror of `detect()`: EMA fan inverted + bearish signal candle.

        `held` is accepted per the `Rule` interface but unused: this rule's exit is the
        bearish-mirror pattern trigger itself (source-02 §2.1), not a stop/target check
        against the specific held setup (that's the backtester/paper trader's job).
        """
        del held
        candles = candles_by_tf.get(self.granularity, [])
        if len(candles) < 2:
            return False

        if regime.detect_condition(candles) != regime.Condition.BEARISH:
            return False

        fan = indicators.ema_fan(candles, periods=self.params["ema_periods"])
        last_idx = len(candles) - 1
        if not indicators.fan_aligned(fan, last_idx, "bearish"):
            return False

        signal_candle = candles[-1]
        if not self._in_entry_zone(signal_candle, fan, last_idx, "bearish"):
            return False

        return self._match_signal_pattern(candles, "bearish") is not None

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _in_entry_zone(
        self,
        candle: Candle,
        fan: dict[int, list[float]],
        idx: int,
        direction: Literal["bullish", "bearish"],
    ) -> bool:
        """Price in the configured `entry_zone`: an EMA-fast touch, or a dip into the
        mid/slow EMA band (source-02 "touches or is below the EMA8" / source-07 "dip into
        the 20-50 EMA band").
        """
        periods = sorted(fan.keys())
        if self.params["entry_zone"] == "ema_touch":
            fast = Decimal(str(fan[periods[0]][idx]))
            if direction == "bullish":
                return candle.low <= fast
            return candle.high >= fast

        mid = Decimal(str(fan[periods[1]][idx]))
        slow = Decimal(str(fan[periods[-1]][idx]))
        band_low, band_high = (mid, slow) if mid <= slow else (slow, mid)
        return candle.low <= band_high and candle.high >= band_low

    def _match_signal_pattern(
        self, candles: list[Candle], direction: Literal["bullish", "bearish"]
    ) -> str | None:
        """First configured pattern (in `signal_patterns` order) that fires on `direction`."""
        last = candles[-1]
        prev = candles[-2] if len(candles) >= 2 else None
        prev2 = candles[-3] if len(candles) >= 3 else None

        for pattern in self.params["signal_patterns"]:
            if pattern == "pin_bar" and candle_lib.is_pin_bar(last) == direction:
                return pattern
            if pattern == "hammer" and direction == "bullish" and candle_lib.is_hammer(last):
                return pattern
            if (
                pattern == "shooting_star"
                and direction == "bearish"
                and candle_lib.is_shooting_star(last)
            ):
                return pattern
            if pattern == "doji" and candle_lib.is_doji(last):
                return pattern
            if pattern == "marubozu" and candle_lib.is_marubozu(last):
                body_dir = "bullish" if candle_lib.body(last) > 0 else "bearish"
                if body_dir == direction:
                    return pattern
            if pattern == "tweezer" and prev is not None:
                result = candle_lib.is_tweezer(prev, last)
                if (direction == "bullish" and result == "bottom") or (
                    direction == "bearish" and result == "top"
                ):
                    return pattern
            if pattern == "three_bar_reversal" and prev is not None and prev2 is not None:
                if candle_lib.is_three_bar_reversal(prev2, prev, last) == direction:
                    return pattern
        return None

    def _compute_stop(self, candles: list[Candle], signal_candle: Candle) -> Decimal | None:
        """Sell-stop below the signal candle's low: a fixed buffer, or 1 ATR (§17.3)."""
        buffer_ticks = self.params["buffer_ticks"]
        if self.params["stop_method"] == "fixed":
            return signal_candle.low - buffer_ticks

        atr_vals = indicators.atr(candles, period=_ATR_PERIOD)
        if not atr_vals:
            return None
        return signal_candle.low - Decimal(str(atr_vals[-1]))

    def _compute_target(
        self, candles: list[Candle], entry: Decimal, stop: Decimal
    ) -> Decimal | None:
        """Buy-limit target per `target_method`: 1:1 measured move, previous swing high, or
        a Fib 1.272 extension of the last swing move (source-02 §2.1, source-07 §7.1/§7.4).
        """
        method = self.params["target_method"]
        risk = entry - stop

        if method == "measured_1to1":
            return entry + risk

        if method == "swing":
            high_idxs = levels.swing_highs(candles)
            if not high_idxs:
                return None
            return candles[high_idxs[-1]].high

        if method == "fib_ext":
            high_idxs = levels.swing_highs(candles)
            low_idxs = levels.swing_lows(candles)
            if not high_idxs or not low_idxs:
                return None
            swing_high = candles[high_idxs[-1]].high
            swing_low = candles[low_idxs[-1]].low
            if swing_high <= swing_low:
                return None
            extensions = indicators.fib_extensions(swing_high, swing_low)
            return extensions.get(_FIB_EXTENSION_RATIO)

        return None
