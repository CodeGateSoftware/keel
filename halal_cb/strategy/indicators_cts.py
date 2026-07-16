"""CTS confluence scorer.

Implements the Confluence Trading Score (CTS, spec §9): additive points across a
fixed set of confluence factors, producing a `CTSResult` used by the evaluation
engine (Task 7) to pick one of the 4 graded entry techniques (spec §9/§17.1):

| CTS score | Entry technique | Posture |
|---|---|---|
| Low       | `confirm_3bar`  | 3-bar-reversal confirmation, smaller size, wider stop |
| Mid       | `signal_candle` | wait for the single signal candle, base size |
| High      | `aggressive`    | market/limit on level touch, larger size, tighter stop |

`score()` takes a plain `context: dict` (the same shape as `Setup.context`,
spec/base.py) so callers (rules, the engine, tests) don't need to depend on any
particular indicator module — each factor is read from a documented context key,
listed below. Pure function, no I/O.

Context keys consumed (all optional; missing keys score as absent/0):
- `condition_aligned: bool`       -- market condition (trend structure) agrees with
  the setup's direction (analysis.regime.Condition).
- `in_pullback: bool`             -- market phase is a pullback/retracement, not a
  run (analysis.regime.Phase.PULLBACK).
- `sr_touches: int`               -- support/resistance touch count at the level in
  play; present when `>= 3` (analysis.levels.find_levels min_touches convention).
- `round_number_proximity: bool`  -- price sits near a round-number/magnet level
  (analysis.levels.is_round_number).
- `deceleration: bool`            -- momentum deceleration into the level
  (analysis.indicators.deceleration).
- `ema_fan_aligned: bool`         -- EMA fan aligned with the trade direction
  (analysis.indicators.fan_aligned).
- `rsi_extreme: bool`             -- RSI at an oversold/overbought extreme
  (analysis.indicators.is_oversold/is_overbought).
- `rsi_divergence: bool`          -- RSI divergence confirms the setup
  (analysis.indicators.rsi_divergence).
- `candlestick_pattern: str|None` -- a recognized signal-candle pattern name
  (analysis.candles.is_pin_bar/is_hammer/is_tweezer/...); present when truthy.
- `fib_confluence: bool`          -- setup lines up with a Fibonacci retracement or
  extension level (analysis.indicators.fib_retracements/fib_extensions).
- `seasonality: bool`             -- low-weight seasonality edge; **off by default
  in v1** (default weight 0, per spec §9).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

# Default weights per confluence factor (spec §9). Points are additive; `seasonality`
# is intentionally weighted 0 (off by default in v1 -- backtested low-weight v2 add).
DEFAULT_WEIGHTS: dict[str, int] = {
    "condition_aligned": 2,
    "in_pullback": 1,
    "sr_touches": 2,
    "round_number_proximity": 1,
    "deceleration": 1,
    "ema_fan_aligned": 2,
    "rsi_extreme": 1,
    "rsi_divergence": 2,
    "candlestick_pattern": 1,
    "fib_confluence": 1,
    "seasonality": 0,
}

_MIN_SR_TOUCHES = 3


@dataclass
class CTSFactor:
    """One scored confluence factor: how many points it contributed and why."""

    name: str
    points: int
    present: bool
    detail: str


@dataclass
class CTSResult:
    """The full CTS breakdown: total score plus every factor considered."""

    total: int
    factors: list[CTSFactor] = field(default_factory=list)


def _bool_check(key: str, label: str) -> Callable[[dict], tuple[bool, str]]:
    """Build a presence-check for a plain boolean context flag."""

    def check(context: dict) -> tuple[bool, str]:
        present = bool(context.get(key, False))
        return present, f"{label}: {'present' if present else 'absent'}"

    return check


def _sr_touches_check(context: dict) -> tuple[bool, str]:
    touches = context.get("sr_touches", 0) or 0
    present = touches >= _MIN_SR_TOUCHES
    return present, f"S/R touches={touches} ({'>=' if present else '<'}{_MIN_SR_TOUCHES})"


def _candlestick_pattern_check(context: dict) -> tuple[bool, str]:
    pattern = context.get("candlestick_pattern")
    present = bool(pattern)
    if present:
        return True, f"candlestick pattern: {pattern}"
    return False, "candlestick pattern: none detected"


# Maps a factor name to the function that reads its presence (and an explanatory
# detail string) out of a `Setup.context`-shaped dict. Factor names not present here
# never score, even if a caller passes a weight for them.
_PRESENCE_CHECKS: dict[str, Callable[[dict], tuple[bool, str]]] = {
    "condition_aligned": _bool_check("condition_aligned", "market condition aligned"),
    "in_pullback": _bool_check("in_pullback", "in pullback phase"),
    "sr_touches": _sr_touches_check,
    "round_number_proximity": _bool_check(
        "round_number_proximity", "round-number/magnet proximity"
    ),
    "deceleration": _bool_check("deceleration", "momentum deceleration"),
    "ema_fan_aligned": _bool_check("ema_fan_aligned", "EMA fan aligned"),
    "rsi_extreme": _bool_check("rsi_extreme", "RSI extreme"),
    "rsi_divergence": _bool_check("rsi_divergence", "RSI divergence"),
    "candlestick_pattern": _candlestick_pattern_check,
    "fib_confluence": _bool_check("fib_confluence", "Fib confluence"),
    "seasonality": _bool_check("seasonality", "seasonality"),
}


def score(context: dict, weights: dict[str, int] | None = None) -> CTSResult:
    """Sum points for every present confluence factor.

    Iterates `weights` (default `DEFAULT_WEIGHTS`, spec §9) in order; for each
    factor name looks up its presence-check, reads `context`, and awards the
    configured weight only when present (absent factors contribute 0). Unknown
    factor names (no registered presence-check) always score absent -- a caller
    can't invent points by stuffing an arbitrary key into `context`.
    """
    weights = DEFAULT_WEIGHTS if weights is None else weights
    factors: list[CTSFactor] = []
    total = 0
    for name, points in weights.items():
        check = _PRESENCE_CHECKS.get(name)
        if check is None:
            present, detail = False, f"{name}: unrecognized factor"
        else:
            present, detail = check(context)
        earned = points if present else 0
        total += earned
        factors.append(CTSFactor(name=name, points=earned, present=present, detail=detail))
    return CTSResult(total=total, factors=factors)


def entry_technique(
    total: int, low: int = 5, high: int = 8
) -> Literal["confirm_3bar", "signal_candle", "aggressive"]:
    """Map a CTS total to the graded entry technique ladder (spec §9/§17.1).

    `total < low` -> `confirm_3bar` (3-bar-reversal confirmation, smaller size,
    wider stop). `low <= total < high` -> `signal_candle` (wait for the single
    signal candle, base size). `total >= high` -> `aggressive` (market/limit on
    level touch, larger size toward the cap, tighter stop).
    """
    if total < low:
        return "confirm_3bar"
    if total < high:
        return "signal_candle"
    return "aggressive"
