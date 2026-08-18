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

**WHY THIS RULE CARRIES INCREMENTAL STATE (#352).** `detect()`/`exit_signal()` are called
once per bar by `strategy.backtest`, each time with the whole prefix of the candle series.
Every full-series read this rule used to make — `regime.detect_phase` (a pivot scan of the
entire prefix), `indicators.ema_fan` (three full EMAs plus a Decimal->float close
conversion of the prefix), `indicators.atr`, `levels.swing_highs`/`swing_lows` — consumed
only its LAST value. A per-bar O(n) recompute of an O(n) series over n bars is O(n^2), and
on a 5-year hourly window that is not an abstraction: measured on a deterministic synthetic
8,784-bar (1y) vs 43,800-bar (5y) fixture, the shipped-defaults backtest ran 8.2s -> 233.5s
when #352 was opened (28.3x for a 5.0x bar count) and 8.9s -> 168.6s on this branch's
reproduction (18.9x) — squarely quadratic either way; cProfile put 71% of the 1y run in
`regime.detect_phase`'s `_swing_highs`/`_swing_lows` and 26% in `indicators.ema_fan`, and
the real-data 5y run that opened #352 was killed at 38+ CPU-minutes. With the running state
below, the same fixtures run 0.2s and 2.6s (the residual 13x-vs-5x superlinearity is the
ENGINE's per-bar `candles[: i + 1]` slice, shared by every rule — not this rule's math).
The turtle rule solved the same shape by deciding on a bounded TAIL of history — acceptable
there because Donchian is exact over its own window and ADX/ATR converge, but NOT here:
EMA(50) seeded four bars back is a different number than EMA(50) seeded five years back, so
a tail would change which setups fire and silently re-parameterize the rule. Instead,
`_RunningState` below keeps the running value of every full-series quantity and extends it
by exactly the appended bars — the same arithmetic the pure functions perform, one bar at a
time, so the results are bit-identical while each bar costs O(1) (amortized; O(gap) when a
caller jumps the window forward, O(n) only when handed an unrelated series, which rebuilds).
The new invariant: the state always describes exactly the prefix `[first_ts .. last_ts]` it
validates against (length + both end timestamps), and any call that does not extend that
prefix rebuilds from the pure functions rather than trusting stale numbers.
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

# `regime._swing_highs`' pivot radius (arm=1): a phase pivot at index i is confirmed once
# candle i+1 exists, by beating candles i-1 and i+1. `levels.swing_highs` uses radius 2
# (lookback=2) for the target pivots; both confirmations are mirrored bar-by-bar in
# `_RunningState.extend`.
_PHASE_PIVOT_RADIUS = 1


class _RunningState:
    """Running values of every full-series quantity `detect()`/`exit_signal()` read, kept in
    lockstep with a growing candle prefix so each new bar costs O(1) instead of O(n) (#352 —
    see the module docstring for why the tail-slice approach used by `TurtleBreakout` was
    rejected here: it would change the numbers, this does not).

    Exactness is the contract. Each `extend` step performs the SAME floating-point
    operations, in the same order, as the corresponding pure function (`indicators.ema`,
    `indicators.atr`, `regime._swing_highs`/`_swing_lows`, `levels.swing_highs`/
    `swing_lows`) applied to the whole prefix, so a value served from this state is
    bit-identical to the one the pure function returns. That equivalence is not asserted
    here but PINNED in `tests/strategy/test_pullback.py`, which walks a deterministic
    series bar-by-bar and compares every state value against the pure recompute.

    A pivot at index i depends only on candles within its radius of i, so it is final the
    moment the last of those candles exists; `extend` therefore confirms exactly one
    candidate pivot per appended bar (the one the new bar completes) and can never miss one
    that a full rescan would find. EMAs and Wilder ATR are one-bar recursive filters whose
    next value needs only the previous output and the new bar — which is the whole reason
    this class exists.
    """

    __slots__ = (
        "atr_last",
        "atr_trs",
        "ema_tail",
        "first_close",
        "first_ts",
        "last_ts",
        "length",
        "phase_high",
        "phase_low",
        "target_high",
        "target_low",
    )

    def __init__(
        self,
        length: int,
        first_ts: int,
        last_ts: int,
        first_close: Decimal,
        ema_tail: dict[int, float],
        phase_high: Decimal | None,
        phase_low: Decimal | None,
        target_high: Decimal | None,
        target_low: Decimal | None,
        atr_trs: list[float],
        atr_last: float,
    ) -> None:
        self.length = length
        self.first_ts = first_ts
        self.last_ts = last_ts
        self.first_close = first_close
        self.ema_tail = ema_tail
        self.phase_high = phase_high
        self.phase_low = phase_low
        self.target_high = target_high
        self.target_low = target_low
        self.atr_trs = atr_trs
        self.atr_last = atr_last

    # -- cache validation -----------------------------------------------------

    def extends(self, candles: list[Candle]) -> bool:
        """True iff `candles` is this state's prefix plus >= 1 appended bars.

        Validated by length and BOTH end timestamps rather than object identity: the
        backtester hands each call a fresh `candles[: i + 1]` slice (new list, same
        `Candle` objects) and the live engine refetches new `Candle` objects each cycle —
        either way, ts are unique within one product's series, so a series that starts
        where this one started and carries this state's last bar at this state's length
        IS this state's prefix. A window that slid, shrank, or belongs to another series
        fails the check and rebuilds.
        """
        return (
            len(candles) > self.length
            and candles[0].ts == self.first_ts
            and candles[self.length - 1].ts == self.last_ts
        )

    # -- construction ----------------------------------------------------------

    @classmethod
    def build(cls, candles: list[Candle], ema_periods: tuple[int, ...]) -> _RunningState:
        """Full O(n) recompute from the pure functions — the cold-start and the fallback for
        any series that does not extend the previous one. Seeding FROM the pure functions
        (rather than a second implementation of them) is what makes the fallback exact by
        construction; only `atr_trs` (the true ranges of the seed window, needed to keep
        extending ATR exactly while fewer than `period` samples exist) is derived here, and
        it reuses `indicators.atr`'s own true-range expression verbatim.
        """
        fan = indicators.ema_fan(candles, periods=ema_periods)
        phase_high_idx = cls._last_pivot(candles, _PHASE_PIVOT_RADIUS, high=True)
        phase_low_idx = cls._last_pivot(candles, _PHASE_PIVOT_RADIUS, high=False)
        target_high_idxs = levels.swing_highs(candles)
        target_low_idxs = levels.swing_lows(candles)

        seed_len = min(_ATR_PERIOD, len(candles))
        atr_trs = [cls._true_range(candles, i) for i in range(seed_len)]

        return cls(
            length=len(candles),
            first_ts=candles[0].ts,
            last_ts=candles[-1].ts,
            first_close=candles[0].close,
            ema_tail={period: fan[period][-1] for period in ema_periods},
            phase_high=candles[phase_high_idx].high if phase_high_idx is not None else None,
            phase_low=candles[phase_low_idx].low if phase_low_idx is not None else None,
            target_high=candles[target_high_idxs[-1]].high if target_high_idxs else None,
            target_low=candles[target_low_idxs[-1]].low if target_low_idxs else None,
            atr_trs=atr_trs,
            atr_last=indicators.atr(candles, period=_ATR_PERIOD)[-1],
        )

    @staticmethod
    def _true_range(candles: list[Candle], i: int) -> float:
        """`indicators.atr`'s own true-range expression, factored out so `build` and `extend`
        compute the identical float for the identical bar."""
        h, low = float(candles[i].high), float(candles[i].low)
        if i == 0:
            return h - low
        prev_close = float(candles[i - 1].close)
        return max(h - low, abs(h - prev_close), abs(low - prev_close))

    @staticmethod
    def _last_pivot(candles: list[Candle], radius: int, high: bool) -> int | None:
        """Index of the LAST pivot in `candles` at `radius` — `regime._swing_highs`'s arm=1
        scan for the phase pivots, `levels.swing_highs`'s lookback=2 scan for the target
        pivots (both define a pivot identically: strictly beyond every neighbour within the
        radius; only the radius differs). Scanned from the end because only the last pivot
        is ever consumed. Returns None when no pivot exists, which the consumers treat
        exactly as `detect_phase`/`_compute_target` treat empty pivot lists.
        """
        n = len(candles)
        for i in range(n - radius - 1, radius - 1, -1):
            if high:
                if all(
                    candles[i].high > candles[j].high
                    for j in range(i - radius, i + radius + 1)
                    if j != i
                ):
                    return i
            elif all(
                candles[i].low < candles[j].low for j in range(i - radius, i + radius + 1) if j != i
            ):
                return i
        return None

    # -- incremental update ----------------------------------------------------

    def extend(self, candles: list[Candle]) -> None:
        """Fold the appended bars (`candles[self.length :]`) into the running values, in
        order, using the same arithmetic the pure functions use — see the class docstring
        for the exactness argument. O(1) per appended bar, so a one-bar step (the
        backtester's steady state) is O(1) and a skipped-ahead window pays only for the
        bars it actually skipped.
        """
        for j in range(self.length, len(candles)):
            c = candles[j]

            # EMA: `indicators.ema`'s step, same operand order — `alpha * v + (1 - alpha) * prev`.
            for period, prev in self.ema_tail.items():
                alpha = 2.0 / (period + 1)
                self.ema_tail[period] = alpha * float(c.close) + (1 - alpha) * prev

            # ATR: seed mean while fewer than `period` true ranges exist (matching atr()'s
            # back-fill of `sum(trs[:seed_len]) / seed_len`), then the Wilder step
            # `(prev * (period - 1) + tr) / period` verbatim.
            tr = self._true_range(candles, j)
            if len(self.atr_trs) < _ATR_PERIOD:
                self.atr_trs.append(tr)
                self.atr_last = sum(self.atr_trs) / len(self.atr_trs)
            else:
                self.atr_last = (self.atr_last * (_ATR_PERIOD - 1) + tr) / _ATR_PERIOD

            # Phase pivots (radius 1): bar j completes the candidacy of candle j-1, which a
            # full rescan would newly include — `regime._swing_highs(range(1, n-1))`'s last
            # confirmable index is exactly n-2.
            if j >= 2:
                prev_bar = candles[j - 1]
                if prev_bar.high > candles[j - 2].high and prev_bar.high > c.high:
                    self.phase_high = prev_bar.high
                if prev_bar.low < candles[j - 2].low and prev_bar.low < c.low:
                    self.phase_low = prev_bar.low

            # Target pivots (radius 2): bar j completes the candidacy of candle j-2 —
            # `levels.swing_highs(range(2, n-2))`'s last confirmable index is exactly n-3.
            if j >= 4:
                cand = candles[j - 2]
                window = (candles[j - 4], candles[j - 3], candles[j - 1], c)
                if all(cand.high > w.high for w in window):
                    self.target_high = cand.high
                if all(cand.low < w.low for w in window):
                    self.target_low = cand.low

        self.length = len(candles)
        self.last_ts = candles[-1].ts


def _tail_aligned(fan_tail: dict[int, float], direction: indicators.Direction) -> bool:
    """`indicators.fan_aligned(fan, idx, direction)` at the LAST index, evaluated on the
    running tail values: same period ordering (fastest = smallest period), same strict
    stacking. Reading `fan[p][idx]` and reading the running `fan[p]` produce the same float
    by `_RunningState`'s contract, so this is the same predicate without the full lists.
    """
    periods = sorted(fan_tail.keys())
    vals = [fan_tail[p] for p in periods]
    if direction == "bullish":
        return all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


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
        # Running per-bar state for the full-series reads (see `_RunningState`/#352). None
        # until the first call with >= 2 candles; `_sync` extends or rebuilds it from there.
        self._state: _RunningState | None = None

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def _sync(self, candles: list[Candle]) -> _RunningState:
        """Bring `self._state` up to `candles` — extend by the appended bars when the cached
        prefix validates, rebuild from the pure functions otherwise — and return it.

        Called at the top of `detect()`/`exit_signal()` BEFORE any gate can return early:
        the state must advance on every bar of the driving loop, not only on bars that get
        deep into the gate chain, or the next call would arrive with a multi-bar gap and
        pay a full rebuild (#352's quadratic path) every time an early gate declined.
        """
        state = self._state
        if state is not None and state.extends(candles):
            state.extend(candles)
        else:
            state = _RunningState.build(candles, self.params["ema_periods"])
            self._state = state
        return state

    def _phase(self, state: _RunningState, candles: list[Candle]) -> regime.Phase:
        """`regime.detect_phase(candles)` evaluated from the running state — same reads
        (both last pivots, first and last close), same comparisons, in the same order.
        """
        if state.phase_high is None or state.phase_low is None:
            return regime.Phase.RUN
        trend_up = candles[-1].close >= state.first_close
        if trend_up:
            if candles[-1].close > state.phase_high:
                return regime.Phase.RUN
            return regime.Phase.PULLBACK
        if candles[-1].close < state.phase_low:
            return regime.Phase.RUN
        return regime.Phase.PULLBACK

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Identify -> Predict -> Decide -> Execute (source-02 §2.0), bullish side only."""
        candles = candles_by_tf.get(self.granularity, [])
        if len(candles) < 2:
            return None
        state = self._sync(candles)

        # Identify: bullish condition + phase-two pullback.
        if regime.detect_condition(candles) != regime.Condition.BULLISH:
            return None
        if self._phase(state, candles) != regime.Phase.PULLBACK:
            return None

        # Predict: EMA fan confirms trend likely to continue.
        fan = state.ema_tail
        if not _tail_aligned(fan, "bullish"):
            return None

        signal_candle = candles[-1]
        if not self._in_entry_zone(signal_candle, fan, "bullish"):
            return None

        # Decide: a qualifying signal candle gives the trigger.
        pattern = self._match_signal_pattern(candles, "bullish")
        if pattern is None:
            return None

        # Execute: buy-stop above the signal high, stop/target per the configured methods.
        entry = signal_candle.high + self.params["buffer_ticks"]
        stop = self._compute_stop(state, signal_candle)
        if stop is None or stop >= entry:
            return None

        target = self._compute_target(state, entry, stop)
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
        state = self._sync(candles)

        if regime.detect_condition(candles) != regime.Condition.BEARISH:
            return False

        fan = state.ema_tail
        if not _tail_aligned(fan, "bearish"):
            return False

        signal_candle = candles[-1]
        if not self._in_entry_zone(signal_candle, fan, "bearish"):
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
        fan_tail: dict[int, float],
        direction: Literal["bullish", "bearish"],
    ) -> bool:
        """Price in the configured `entry_zone`: an EMA-fast touch, or a dip into the
        mid/slow EMA band (source-02 "touches or is below the EMA8" / source-07 "dip into
        the 20-50 EMA band").

        Takes the RUNNING EMA values (`_RunningState.ema_tail`) rather than a full fan of
        lists: only the last index was ever read, and `Decimal(str(value))` of the same
        float yields the same Decimal either way.
        """
        periods = sorted(fan_tail.keys())
        if self.params["entry_zone"] == "ema_touch":
            fast = Decimal(str(fan_tail[periods[0]]))
            if direction == "bullish":
                return candle.low <= fast
            return candle.high >= fast

        mid = Decimal(str(fan_tail[periods[1]]))
        slow = Decimal(str(fan_tail[periods[-1]]))
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

    def _compute_stop(self, state: _RunningState, signal_candle: Candle) -> Decimal | None:
        """Sell-stop below the signal candle's low: a fixed buffer, or 1 ATR (§17.3).

        The ATR branch reads the running value — identical to `indicators.atr(candles,
        period=_ATR_PERIOD)[-1]` by `_RunningState`'s contract, without the O(n) recompute.
        """
        buffer_ticks = self.params["buffer_ticks"]
        if self.params["stop_method"] == "fixed":
            return signal_candle.low - buffer_ticks

        return signal_candle.low - Decimal(str(state.atr_last))

    def _compute_target(
        self, state: _RunningState, entry: Decimal, stop: Decimal
    ) -> Decimal | None:
        """Buy-limit target per `target_method`: 1:1 measured move, previous swing high, or
        a Fib 1.272 extension of the last swing move (source-02 §2.1, source-07 §7.1/§7.4).

        The swing/fib branches read the running last-pivot values — identical to
        `candles[levels.swing_highs(candles)[-1]].high` (and the low mirror) by
        `_RunningState`'s contract, without the O(n) rescans.
        """
        method = self.params["target_method"]
        risk = entry - stop

        if method == "measured_1to1":
            return entry + risk

        if method == "swing":
            if state.target_high is None:
                return None
            return state.target_high

        if method == "fib_ext":
            if state.target_high is None or state.target_low is None:
                return None
            swing_high = state.target_high
            swing_low = state.target_low
            if swing_high <= swing_low:
                return None
            extensions = indicators.fib_extensions(swing_high, swing_low)
            return extensions.get(_FIB_EXTENSION_RATIO)

        return None
