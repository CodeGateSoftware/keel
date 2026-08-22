"""The per-family, ratchet-only stop-management policy for held positions (#442).

`execution/executor.py` implements three exit primitives -- `trail_stop_atr`,
`roll_to_break_even`, `scale_out` -- that have ZERO callers on the live path: live exits
ride ONE native Coinbase trigger-bracket placed at entry (the exchange owns the
stop-vs-target race), the broker port has no bracket/OCO order kind to express a
cancel-and-replace through, and the agent cycle has no per-bar stop-management step at
all. That live wiring is split out to issue #502; it is NOT this module's concern.

What this module IS: the exit POLICY those live primitives encode -- an ATR-multiple
trailing stop and a break-even roll, both strictly ratchet-only (a stop may move toward
profit, never away from it) -- expressed as pure functions the engines that DO drive
exits per bar can apply:

- `strategy.backtest.backtest` (the single-rule, production-faithful backtester), and
- `sim.portfolio_sim._process_held` (the account simulator's exit resolution).

Both engines apply it with the SAME sequencing, which is the no-lookahead contract:

1. touch checks for a bar run against the stop level carried INTO that bar (the level
   management produced from COMPLETED prior bars -- live, the bracket rests at the old
   level until a management cycle replaces it, and this mirrors that exactly);
2. only if the position survives the bar does `next_stop` run, on that bar's own
   high/close and an ATR over a window ending at that bar.

**Where the policy lives (design decision).** The knobs are per-rule-family constructor
params (`trail_atr_mult`, `be_roll_rr`, mirroring the existing `atr_mult`-style naming),
NOT a global config block: exit behavior is a property of a rule family in the same way
`stop_method` is, and a global knob could not express "turtle does not trail". A rule
whose `params` carry neither knob gets `EXIT_POLICY_OFF` and trades exactly as before
the wiring existed -- pinned by tests on both engines.

**Why turtle is deliberately not offered the knobs** (#442 hypothesis 3, confirmed):
`turtle_breakout`'s real exit is the asymmetric Donchian channel (`exit_signal`), with
`target_rr=6` only a nominal level that exists to clear the engine's rr>=1 gate. A
trailing stop or a break-even roll would cut the rare long winners a low-win-rate
trend-follower exists to let run. `policy_for` therefore reads turtle as OFF, and the
rule's own PARAM_DOCS/module docstring say so.

**Ratchet vs rail 9.** The live invariant ("no stop-loss widening", guards.py rail 9)
compares a proposed protective stop against the last recorded one and vetoes any
strictly lower proposal. `next_stop` starts from the current stop and only ever takes
maxima, so it can never emit a wider stop -- the sim-side twin of
`tests/execution/test_executor.py::test_a_ratchet_only_trail_can_never_trip_rail_9`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.analysis.indicators import atr as atr_of
from keel.strategy.rules.base import Rule
from keel.types import Candle

__all__ = [
    "DEFAULT_TRAIL_ATR_PERIOD",
    "EXIT_POLICY_OFF",
    "ExitPolicy",
    "next_stop",
    "policy_for",
    "trailing_atr",
]

#: The ATR length the trailing arm computes from when the rule does not declare its own
#: (`rsi_meanrev` and `turtle_breakout` carry `atr_period`; `pullback_continuation`'s
#: own stop machinery hardcodes 14 -- `_ATR_PERIOD` -- so 14 is the family-consistent
#: default here too).
DEFAULT_TRAIL_ATR_PERIOD = 14


@dataclass(frozen=True)
class ExitPolicy:
    """One held position's stop-management knobs, read off the owning rule's `params`.

    `trail_atr_mult` -- trail the stop `mult x ATR` below the bar's close, ratchet-only.
    `be_roll_rr` -- once the bar's high clears `entry + be_roll_rr x` the ORIGINAL
    per-unit risk (`entry - initial stop`), move the stop to the entry. Either may be
    `None` (that arm is off); both `None` is `EXIT_POLICY_OFF`.
    """

    trail_atr_mult: Decimal | None
    be_roll_rr: Decimal | None
    atr_period: int


#: The policy every rule without exit knobs gets: nothing ever moves the stop.
EXIT_POLICY_OFF = ExitPolicy(
    trail_atr_mult=None, be_roll_rr=None, atr_period=DEFAULT_TRAIL_ATR_PERIOD
)


def _as_decimal(value: object) -> Decimal | None:
    """Coerce a param value to `Decimal`, or `None` for `None`.

    Rules constructed in Python carry real `Decimal`s already; rows rebuilt through
    `agent.build_rule_from_params` arrive coerced (the knobs are in `_DECIMAL_PARAMS`);
    this defense-in-depth keeps a hand-built rule from raising mid-backtest on a value
    that was always meant to be a number.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def policy_for(rule: Rule) -> ExitPolicy:
    """The stop-management policy for `rule`, read off its own `params`.

    A rule that carries neither knob gets `EXIT_POLICY_OFF` -- this is the branch turtle
    and every pre-#442 rule row takes, and it is what makes the wiring default-off for
    everything that never asked for it.
    """
    params = getattr(rule, "params", None) or {}
    trail = _as_decimal(params.get("trail_atr_mult"))
    be = _as_decimal(params.get("be_roll_rr"))
    if trail is None and be is None:
        return EXIT_POLICY_OFF

    raw_period = params.get("atr_period", DEFAULT_TRAIL_ATR_PERIOD)
    try:
        atr_period = int(raw_period)
    except (TypeError, ValueError):
        atr_period = DEFAULT_TRAIL_ATR_PERIOD
    if atr_period <= 0:
        atr_period = DEFAULT_TRAIL_ATR_PERIOD
    return ExitPolicy(trail_atr_mult=trail, be_roll_rr=be, atr_period=atr_period)


def trailing_atr(candles: list[Candle], period: int) -> Decimal | None:
    """The current ATR the trailing arm trails behind, over a bounded tail.

    Wilder-smoothed ATR converges, so a `4 x period + 1` bar tail (the same bound
    `turtle_breakout.detect` uses for its own ATR/ADX reads) prices the trail without an
    O(n) recompute per bar on the engine's growing prefix. `None` until `period + 1`
    bars exist: a Wilder average back-filled from fewer than a full period of true
    ranges is a seed, not a statistic, and trailing off it would move stops on noise the
    rule never asked to measure.
    """
    if len(candles) < period + 1:
        return None
    tail = candles[-(4 * period + 1) :]
    values = atr_of(tail, period)
    if not values:
        return None
    value = Decimal(str(values[-1]))
    return value if value > 0 else None


def next_stop(
    policy: ExitPolicy,
    entry: Decimal,
    initial_stop: Decimal,
    current_stop: Decimal,
    candle: Candle,
    atr: Decimal | None,
) -> Decimal:
    """The stop level `policy` leaves the position with AFTER `candle` completes.

    Ratchet-only by construction: the result starts at `current_stop` and only ever
    takes maxima over the arms' proposals, so it can never be lower than the stop the
    position carried in -- the sim-side statement of rail 9's no-widening invariant.

    `entry`/`initial_stop` are the position's ACHIEVED fill and its ORIGINAL setup stop
    (the pair `_close_trade`/`_process_held` already measure R-multiples against), so
    the break-even threshold `be_roll_rr` clears is expressed in the same original-risk
    units the trade was sized on, never in the (already-raised) current stop's units.

    The trailing arm trails the bar's CLOSE (the live primitive's `current_price` at a
    cycle boundary is the latest price, i.e. the close on bar data); the break-even arm
    triggers on the bar's HIGH (the threshold is "the trade reached this profit", the
    MFE convention the engines already track).
    """
    stop = current_stop

    if policy.be_roll_rr is not None:
        initial_risk = entry - initial_stop
        if initial_risk > 0 and (candle.high - entry) >= policy.be_roll_rr * initial_risk:
            stop = max(stop, entry)

    if policy.trail_atr_mult is not None and atr is not None:
        stop = max(stop, candle.close - policy.trail_atr_mult * atr)

    return stop
