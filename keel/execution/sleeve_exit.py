"""The sleeve exit monitor: where a held product stands against its structural exit levels (#857,
spec §7, plan P15 Task 15.1).

**Spec §7's two layers, and this is the first.** The MONITOR is pure and rule-less: for every
product holding an open tranche with no resting bracket, the cycle (`agent._watch_sleeve_exits`)
hands `classify` the completed daily bars and records the answer in
`agent_state["sleeve_exit:<product>"]`, emitting a `sleeve.exit_watch` event only when the level
CHANGES. The RULE, `sleeve_exit` (P16), is the second layer: a preview-only `reduce_signal` that
proposes the whole sleeve when the monitor says `breached`. This module places, cancels and
writes nothing, and reads no repository, broker or clock -- it is arithmetic over candles.

**The two arms**, each judged on completed daily bars:

* `drawdown` -- the trailing stop: `dd_level = high x (1 - dd_pct / 100)`, where `high` is the
  highest bar HIGH of the last `lookback_days` bars (fewer when fewer are cached: the high of
  what is held is still a high the product reached). Breached when the latest close is strictly
  under `dd_level`; a close AT the level is not a breach.
* `sma` -- the structural break: the `sma_period` simple moving average of closes, ending at each
  of the last `confirm_days` bars. Breached when every one of those closes is strictly under its
  own SMA. It needs `sma_period + confirm_days - 1` bars, or it is not judged.

**The level** is `breached` when any requested arm breached (`breached_arms` names them, in arm
order), `insufficient_history` when EVERY requested arm lacks the bars to be judged, and otherwise
`near` or `clear` by the nearest distance `(close - level) / level` over the arms that were
judged. A close already under the SMA but not yet confirmed has a negative distance, so it reads
`near`.

**R26: the default levels are module constants** -- `DEFAULT_DD_PCT` 35, `DEFAULT_LOOKBACK_DAYS`
200, `DEFAULT_SMA_PERIOD` 200, `DEFAULT_CONFIRM_DAYS` 3, `DEFAULT_WARN_PCT` 5, spec §7's values.
A `sleeve_exit` rule's params override them per product; there is no config key, because a
config key is a second place for the numbers to disagree. WHICH rule sets a product's levels is
the rule module's to say (`keel.strategy.rules.sleeve_exit.monitor_rule`/`monitor_params`,
selected by the registered class since P16 retired R69's duck-typed read), so this module keeps
its one import, `keel.types`. They are an operator alert, not a
tested edge: nothing here was tuned (the research freeze, 2026-09-27).

**Spec §7's failure modes, and where each is handled:**

(a) *The stop contradicts the sleeve's thesis* -- a DCA sleeve keeps buying through the drawdown
    a stop would sell into. So the monitor only ALERTS, and the rule only proposes (S2): nothing
    here, or anywhere the monitor reaches, can sell.
(b) *Too little history for a 200-day SMA* -- the arm reports `insufficient_history` and does not
    fire; it is never guessed from fewer bars.
(c) *The monitor is silent because no cycle ran* -- doctor's existing `profile.cycled`, not this
    module's to add. #811's `position.unmanaged` is a different failure and stays where it is.
(d) *A level that flaps* -- `near` has hysteresis: a product enters `near` within `warn_pct` of a
    level and leaves it only beyond `2 x warn_pct`. A product recovering from `breached` leaves
    through the same wide band, so a close a hair over the level does not read `clear` and then
    `breached` again the next day. `warn_pct` 0 turns the `near` band off.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from keel.types import Candle

#: R26 / spec §7: the trailing drawdown, in percent under the lookback high.
DEFAULT_DD_PCT = Decimal("35")
#: R26 / spec §7: the bars the trailing high is taken over.
DEFAULT_LOOKBACK_DAYS = 200
#: R26 / spec §7: the moving average the structural break is judged against.
DEFAULT_SMA_PERIOD = 200
#: R26 / spec §7: consecutive closes under the SMA that confirm a break.
DEFAULT_CONFIRM_DAYS = 3
#: R26 / spec §7: how close to a level, in percent, reads `near` (and `2 x` it leaves `near`).
DEFAULT_WARN_PCT = Decimal("5")

#: The `agent_state` key prefix the cycle records each watched product's level under.
STATE_PREFIX = "sleeve_exit:"

Level = Literal["clear", "near", "breached", "insufficient_history"]
Arm = Literal["drawdown", "sma"]

#: Every arm `classify` knows, in the order `breached_arms` names them.
ARMS: tuple[Arm, ...] = ("drawdown", "sma")

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ExitWatch:
    """One product's classification on its latest completed daily bar.

    `close` and `ts` are that bar's; `dd_level` is the drawdown arm's level and `sma` the SMA
    ending at that bar -- each `None` when its arm was not requested or had too few bars.
    `previous` is the level the caller last recorded (`None` for a product seen for the first
    time), carried so a notification can tell a recovery from a first observation."""

    product_id: str
    level: Level
    close: Decimal | None
    dd_level: Decimal | None
    sma: Decimal | None
    breached_arms: tuple[str, ...]
    ts: int | None
    previous: Level | None = None


def _sma(closes: Sequence[Decimal], end: int, period: int) -> Decimal:
    """The `period` simple moving average of `closes` ending at index `end` (inclusive)."""
    window = closes[end - period + 1 : end + 1]
    return sum(window, Decimal("0")) / Decimal(period)


def classify(
    product_id: str,
    daily: Sequence[Candle],
    *,
    previous: Level | None = None,
    dd_pct: Decimal = DEFAULT_DD_PCT,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sma_period: int = DEFAULT_SMA_PERIOD,
    confirm_days: int = DEFAULT_CONFIRM_DAYS,
    warn_pct: Decimal = DEFAULT_WARN_PCT,
    arms: Sequence[str] = ARMS,
) -> ExitWatch:
    """Classify `product_id` on its completed daily bars (the module docstring's rules).

    `daily` must hold COMPLETED bars only, oldest first -- the caller drops a still-forming bar
    (`completed_days`). `previous` is the level last recorded for the product; it decides the
    `near` band's hysteresis and nothing else. Raises `ValueError` for an empty, repeated or
    unknown arm set -- a monitor with no arm would read `insufficient_history` forever."""
    requested = tuple(arms)
    if not requested or len(set(requested)) != len(requested) or set(requested) - set(ARMS):
        raise ValueError(f"arms must be a non-empty subset of {ARMS}, got {requested!r}")
    if not daily:
        return ExitWatch(product_id, "insufficient_history", None, None, None, (), None, previous)

    close = daily[-1].close
    distances: list[Decimal] = []
    breached: list[str] = []

    dd_level: Decimal | None = None
    if "drawdown" in requested:
        high = max(c.high for c in daily[-lookback_days:])
        dd_level = high * (Decimal("1") - dd_pct / _HUNDRED)
        if close < dd_level:
            breached.append("drawdown")
        elif dd_level > 0:
            distances.append((close - dd_level) / dd_level)

    sma: Decimal | None = None
    if "sma" in requested and len(daily) >= sma_period + confirm_days - 1:
        closes = [c.close for c in daily]
        last = len(closes) - 1
        sma = _sma(closes, last, sma_period)
        confirming = range(last - confirm_days + 1, last + 1)
        confirmed = all(closes[i] < _sma(closes, i, sma_period) for i in confirming)
        if confirmed:
            breached.append("sma")
        elif sma > 0:
            distances.append((close - sma) / sma)

    judged = dd_level is not None or sma is not None
    if breached:
        level: Level = "breached"
    elif not judged:
        level = "insufficient_history"
    else:
        # Hysteresis (failure mode d): the band a product is already in -- or is coming back
        # from -- is twice as wide as the band it enters at.
        band = warn_pct * (2 if previous in ("near", "breached") else 1)
        nearest = min(distances) * _HUNDRED if distances else None
        level = "near" if warn_pct > 0 and nearest is not None and nearest <= band else "clear"
    return ExitWatch(
        product_id, level, close, dd_level, sma, tuple(breached), daily[-1].ts, previous
    )


def history_days(
    *,
    dd_pct: Decimal = DEFAULT_DD_PCT,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sma_period: int = DEFAULT_SMA_PERIOD,
    confirm_days: int = DEFAULT_CONFIRM_DAYS,
    warn_pct: Decimal = DEFAULT_WARN_PCT,
    arms: Sequence[str] = ARMS,
) -> int:
    """How many days of daily history `classify` needs to judge every requested arm in full: the
    drawdown's `lookback_days` or the SMA's `sma_period + confirm_days - 1` bars, whichever is
    longer, plus one day -- a history window's start is aligned UP to the next bar, which costs
    one. Takes `classify`'s keywords (so `monitor_params` splats into both); only the arms and
    their windows matter. The cycle fills a watched product's cache to this (#938)."""
    del dd_pct, warn_pct  # levels, not windows
    needs = [0]
    if "drawdown" in arms:
        needs.append(lookback_days)
    if "sma" in arms:
        needs.append(sma_period + confirm_days - 1)
    return max(needs) + 1
