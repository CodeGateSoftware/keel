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
The state is acquired LAZILY (`_state_for`, #363 finding 2): a bar the bounded
`regime.detect_condition` gate declines never touches it, and the next state-consuming call
extends across the whole skipped gap. The new invariant: the state always describes exactly
the prefix `[first_ts .. last_ts]` it validates against — length, both end timestamps, AND
the cached boundary bar's close (#363 finding 1: ts alone is not discriminating) — and any
call whose window does not match or extend those anchors rebuilds from the pure functions
rather than trusting stale numbers.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from keel.analysis import candles as candle_lib
from keel.analysis import indicators, levels, regime
from keel.strategy.rules.base import ParamSpec, Rule, Setup
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
        "last_close",
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
        last_close: Decimal,
    ) -> None:
        self.length = length
        self.first_ts = first_ts
        self.last_ts = last_ts
        self.first_close = first_close
        self.last_close = last_close
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

        Validated by four anchors — length, the first ts, and the cached boundary bar's
        ts AND CLOSE — rather than object identity, because identity is unavailable (the
        backtester hands each call a fresh `candles[: i + 1]` slice) and ts alone is not
        discriminating (#363 finding 1): two different series can share `candles[0].ts`
        and the ts at `length - 1` (different products on the same hourly grid), and a
        repair can rewrite a bar's values in place under the same ts. The close anchor
        catches both collision classes for the one bar where the cached state is most
        exposed — its own boundary — at O(1) cost.

        The residual, stated honestly: with these anchors, accidental staleness requires
        a MID-SERIES rewrite that leaves both anchor bars (the first, and the cached
        boundary bar) identical in ts and close. Where the callers actually stand on
        that: `strategy.backtest` and `sim.portfolio_sim` feed prefixes/suffix-windows
        of one fixed in-memory list that is never rewritten mid-run, and the live agent
        rebuilds its Rule instances from scratch each cycle (`agent.py`'s per-cycle
        `_build_rule`) — so nothing that can rewrite history mid-series reuses a cached
        state today. A caller that upserts corrected bars into a series it also drives
        this rule with must discard the rule's state when it rewrites.

        Where extension fires at all: only `strategy.backtest` (strictly-growing
        prefixes) and `sim.portfolio_sim`'s warm-up (its window grows bar-by-bar until
        `WINDOW_BARS` fills, then slides — and a slide changes the first ts, so it
        rebuilds by the first anchor). The live agent never extends; each cycle's rule
        instance starts cold.
        """
        return (
            len(candles) > self.length
            and candles[0].ts == self.first_ts
            and candles[self.length - 1].ts == self.last_ts
            and candles[self.length - 1].close == self.last_close
        )

    def matches(self, candles: list[Candle]) -> bool:
        """True iff `candles` is anchor-identical to exactly the prefix this state
        describes — same length, same first/last ts, same last close (#363 finding 3).

        This is the same-window no-op of `_state_for`: a call that re-reads a window the
        state already describes (the agent and portfolio_sim call `exit_signal` then
        `detect` on one window within a cycle) reuses the resolved state unchanged —
        no rebuild, no extend. When `len(candles) == self.length` the boundary bar IS
        the last bar, so the anchors checked are the same four `extends` checks.
        """
        return (
            len(candles) == self.length
            and candles[0].ts == self.first_ts
            and candles[-1].ts == self.last_ts
            and candles[-1].close == self.last_close
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
            last_close=candles[-1].close,
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
        bars it actually skipped — including a MULTI-bar gap left by calls whose gates
        declined before touching state (#363 finding 2's lazy acquisition).
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
        self.last_close = candles[-1].close


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

    # Per-parameter docstrings, AT THE CLASS (issue #390 C4 / PRD O8): the single source
    # `keel.commands.rules.describe_params` renders by introspection -- the tunable knobs
    # the module docstring names, each with its plain-English meaning and direction.
    PARAM_DOCS: dict[str, str] = {
        "granularity": "the candle series the rule decides on (ONE_HOUR default).",
        "ema_periods": (
            "the EMA fan that defines the trend (default 8/20/50). A wider fan = "
            "fewer, better-established trends."
        ),
        "entry_zone": (
            "where the pullback must land: 'ema_touch' (touch of the fastest EMA) or "
            "'ema_band' (dip into the band between the two slower EMAs)."
        ),
        "signal_patterns": (
            "candle patterns that confirm the pullback has ended (default pin_bar) -- "
            "names the rule can actually match."
        ),
        "buffer_ticks": "price buffer around the entry zone, in quote units.",
        "stop_method": "'fixed' (below the zone) or 'atr' (a multiple of ATR).",
        "target_method": (
            "'measured_1to1', 'swing' (the previous swing high) or 'fib_ext' (the Fib "
            "1.272 extension)."
        ),
        "trail_atr_mult": (
            "ratchet-only trailing stop this many ATRs below each bar's close, once the "
            "trade is open. Default off: measured WORSE than the static exit at the 120 bp "
            "fee (docs/experiments/2026-08-22-trailing-vs-static-exits.md). Sim/backtest "
            "engines only -- live stop management is issue #502."
        ),
        "be_roll_rr": (
            "roll the stop to the entry once the trade has been up this many R. Default "
            "off: measured no-better than the static exit at the 120 bp fee "
            "(docs/experiments/2026-08-22-trailing-vs-static-exits.md). Sim/backtest "
            "engines only -- live stop management is issue #502."
        ),
    }

    # The declared parameter space (issue #528): what a sweep may legitimately explore on
    # this rule, stated HERE so the trials count is derived from the rule rather than
    # remembered by the operator. Ranges are the ones the #476 Optuna study pinned; the
    # three fan slots are DISJOINT by design (a suggestion inside them cannot invert the
    # fan), each naming the ONE `ema_periods` tuple kwarg it feeds via `param=`. Steps are
    # whole periods for the fan and one tick for the buffer -- the grid the space is
    # COUNTED at, never a record of the sampler: the #476 TPE study drew step-1 ints (on
    # the fan's grid) and continuous floats (off the buffer's).
    _PARAM_SPACE: tuple[ParamSpec, ...] = (
        ParamSpec("ema_fast", "int", 5, 12, Decimal(1), param="ema_periods"),
        ParamSpec("ema_mid", "int", 15, 30, Decimal(1), param="ema_periods"),
        ParamSpec("ema_slow", "int", 40, 70, Decimal(1), param="ema_periods"),
        ParamSpec("buffer_ticks", "decimal", 0.01, 0.05, Decimal("0.01")),
    )

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
        trail_atr_mult: Decimal | None = None,
        be_roll_rr: Decimal | None = None,
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
            "trail_atr_mult": trail_atr_mult,
            "be_roll_rr": be_roll_rr,
        }
        # Running per-bar state for the full-series reads (see `_RunningState`/#352). None
        # until the first call whose gates get past `regime.detect_condition` (#363 finding
        # 2); `_state_for` then matches/extends/rebuilds it lazily from there.
        self._state: _RunningState | None = None

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def _state_for(self, candles: list[Candle]) -> _RunningState:
        """Bring `self._state` up to `candles` — validate, extend, or rebuild — and return
        it. This is the LAZY state-acquisition step `detect()`/`exit_signal()` call at
        their first state-CONSUMING site, never at the top (#363 finding 2: acquiring
        before the gates made every call pay the full O(n) `build()` even when the
        bounded `regime.detect_condition` gate — lookback 20 — declined the bar three
        lines later, measured 11.1 ms vs main's 1.8 ms per call (~6-8x, reviewer and
        reproduction) on portfolio_sim's sliding 8,760-bar window). A bar whose gates
        all decline early never touches state; the next
        state-consuming call then sees a multi-bar gap, which `extends`/`extend` fold
        bar-by-bar exactly (O(gap), pinned equal to the pure recompute by the tests).

        Idempotent within a call WITHOUT an object-identity memo: the first resolution in
        a call leaves the state describing exactly `candles`, so every later
        `_state_for(candles)` in that same call takes the O(1) `matches` path — one
        `detect()` resolves (builds or extends) at most once. Identity is deliberately
        NOT trusted across calls: the same list object can be rewritten in place
        (finding 1), so the anchors are re-checked every time.

        Three outcomes, cheapest-first:
          - `matches` — same length and all four anchors: the state already describes
            this window; return it unchanged, no rebuild, no extend (finding 3: the
            agent and portfolio_sim call `exit_signal` then `detect` on the SAME window
            within one cycle, and that second call must not pay a full rebuild).
          - `extends` — strictly longer and the cached boundary bar still validates:
            fold in the appended bars (O(1) each, O(gap) across a declined stretch).
          - otherwise — rebuild from the pure functions (cold start, slid/shrunk window,
            unrelated or rewritten series).
        """
        state = self._state
        if state is not None and state.matches(candles):
            return state
        if state is not None and state.extends(candles):
            state.extend(candles)
            return state
        state = _RunningState.build(candles, self.params["ema_periods"])
        self._state = state
        return state

    def _sync(self, candles: list[Candle]) -> _RunningState:
        """Eager form of `_state_for` — resolve NOW and return the state. `detect()`/
        `exit_signal()` do NOT call this (they acquire state lazily at each first
        state-consuming site, #363 finding 2); it is the entry point the #352
        equivalence tests drive directly and remains exactly the operation a caller
        that needs the state up front wants.
        """
        return self._state_for(candles)

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
        """Identify -> Predict -> Decide -> Execute (source-02 §2.0), bullish side only.

        State is acquired LAZILY (#363 finding 2): the bounded condition gate runs before
        any `_state_for` call, so a non-BULLISH bar never pays the O(n) state work, and
        every site below that does read a state value resolves it through
        `_state_for(candles)` — at most one build/extend per call (see its docstring).
        """
        candles = candles_by_tf.get(self.granularity, [])
        if len(candles) < 2:
            return None

        # Identify: bullish condition + phase-two pullback.
        if regime.detect_condition(candles) != regime.Condition.BULLISH:
            return None
        if self._phase(self._state_for(candles), candles) != regime.Phase.PULLBACK:
            return None

        # Predict: EMA fan confirms trend likely to continue.
        fan = self._state_for(candles).ema_tail
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
        stop = self._compute_stop(self._state_for(candles), signal_candle)
        if stop is None or stop >= entry:
            return None

        target = self._compute_target(self._state_for(candles), entry, stop)
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

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """The bearish mirror of `detect()`: EMA fan inverted + bearish signal candle.

        `held` is accepted per the `Rule` interface but unused: this rule's exit is the
        bearish-mirror pattern trigger itself (source-02 §2.1), not a stop/target check
        against the specific held setup (that's the backtester/paper trader's job).

        State is acquired lazily exactly as in `detect()` (#363 finding 2): a non-BEARISH
        bar declines at the bounded condition gate without paying any state work.
        """
        del held
        candles = candles_by_tf.get(self.granularity, [])
        if len(candles) < 2:
            return False

        if regime.detect_condition(candles) != regime.Condition.BEARISH:
            return False

        fan = self._state_for(candles).ema_tail
        if not _tail_aligned(fan, "bearish"):
            return False

        signal_candle = candles[-1]
        if not self._in_entry_zone(signal_candle, fan, "bearish"):
            return False

        return self._match_signal_pattern(candles, "bearish") is not None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }

    def param_space(self) -> tuple[ParamSpec, ...]:
        return self._PARAM_SPACE

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
