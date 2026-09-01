"""Triple-barrier exits with a vertical time stop, barriers sized to friction (#342).

Research brief B-2, and the other half of the source `cusum_event` implements the entry half of.
Three barriers close a position: an upper (take-profit), a lower (stop), and a **vertical** one
-- sell at the close after N bars, whatever price has done. No shipped rule has the third.

Source: Grądzki et al., *Financial Innovation*, 2025-12-15 (24-period vertical barrier with
±2.5-5% horizontal barriers beat next-bar labeling; vol-adjusted barriers did NOT help);
arXiv 2504.02249 (vertical barrier grid-searched for LABEL BALANCE, with no costs modelled --
label balance is not P&L, and that paper is not evidence about profit); Alvarez 2019 (an
indicator-or-time-stop exit is the industry baseline; the limit-order half that helped there is
unavailable to keel, which fills at market).

**THE BARRIERS ARE MULTIPLES OF PER-PRODUCT FRICTION, and that is #342's design ask.** The
source's ±2.5-5% barriers sit at or below one round trip on this venue, so transplanted as
percentages they are mechanically dead -- a target that pays for the trade and nothing else, and
a stop inside the noise the fee already imposes. Worse, a FIXED percentage is wrong per asset:
since #259 the backtest prices thin books up to 183.8bp per leg, so the same 5% barrier is four
round trips on BTC and barely one on the corpus tail. So the barriers are sized from the
PRODUCT'S OWN friction, computed from its own candles.

⚠️ **THE HONEST PRIOR, TIGHTENED BY MEASUREMENT RATHER THAN INHERITED.** #341 filed this with
the null as its prior. `docs/experiments/2026-09-01-cusum-event-first-measurement.md` then
measured the entry half and found something sharper: at ZERO fee `cusum_event` clears PF 1.0 on
only 8 of 24 assets, median 0.925. The entry has no gross edge for any exit to harvest. A better
exit redistributes P&L across trades; it cannot manufacture an edge that is absent before costs.
This ships to be measured, and the measurement's job is to say by how much a better exit moves a
rule that is losing at zero cost -- not whether it rescues it.
"""

from __future__ import annotations

from decimal import Decimal

from keel.compliance.screen import median_daily_quote_volume
from keel.data.history import GRANULARITY_SECONDS
from keel.strategy.backtest import TAKER_FEE_PCT, slippage_for_quote_volume
from keel.strategy.rules.base import ParamSpec, Rule, Setup
from keel.strategy.rules.cusum_event import cusum_read
from keel.types import Candle, Granularity

#: Seconds in a day, for scaling a per-bar volume statistic to the daily one the slippage model
#: is anchored on.
_SECONDS_PER_DAY = 86_400


def per_product_round_trip(candles: list[Candle], granularity: Granularity) -> Decimal:
    """One round trip's cost for THIS product: two taker legs plus two slippage legs.

    ⚠️ **`median_daily_quote_volume` returns a PER-BAR median despite its name**, so on hourly
    candles it is an hourly figure. `slippage_for_quote_volume` is anchored on a $500M DAILY
    volume, so handing it the hourly number unscaled reports every asset as maximally thin and
    clamps the whole universe to the 183.8bp cap -- every barrier four times too wide, silently,
    with no error anywhere. The bars-per-day scaling below is the fix, and `GRANULARITY_SECONDS`
    is the one duration table rather than a second one written here.

    A bounded tail, not the full history: this runs once per bar in a sim, and the statistic is
    a liquidity proxy whose whole purpose is to be approximately right. The window is declared
    (`liquidity_bars`) for the same reason the CUSUM window is -- an estimate computed over a
    different span is a different estimate, and a hidden one would make backtest and live
    disagree for reasons nobody could see.
    """
    bars_per_day = Decimal(_SECONDS_PER_DAY) / Decimal(GRANULARITY_SECONDS[granularity])
    daily_quote_volume = median_daily_quote_volume(candles) * bars_per_day
    return 2 * TAKER_FEE_PCT + 2 * slippage_for_quote_volume(daily_quote_volume)


class TripleBarrier(Rule):
    """CUSUM entry, friction-sized horizontal barriers, and a vertical time stop.

    The entry is `cusum_event`'s filter, reused rather than reimplemented: it is the sampling
    method this source pairs with these barriers, and it is already measured, so holding it
    fixed makes the exit the only thing that changed. `cusum_event` is the control.
    """

    decimal_params = ("entry_friction_mult", "target_friction_mult", "stop_friction_mult")
    granularity_param = "granularity"

    PARAM_DOCS = {
        "granularity": "Bar size for the filter, the barriers and the time stop.",
        "lookback": "Bars the CUSUM filter is replayed across. Defines the state, not a budget.",
        "liquidity_bars": "Bars the per-product volume statistic is estimated over.",
        "entry_friction_mult": "Event threshold as a multiple of this product's round trip.",
        "target_friction_mult": "Upper barrier, in round trips above the entry.",
        "stop_friction_mult": "Lower barrier, in round trips below the entry.",
        "max_holding_bars": (
            "The VERTICAL barrier: sell at the close once the position is this many bars old, "
            "whatever price has done. Executable under market fills, unlike a resting order."
        ),
    }

    def __init__(
        self,
        product_id: str,
        granularity: Granularity = Granularity.ONE_HOUR,
        lookback: int = 168,
        liquidity_bars: int = 720,
        entry_friction_mult: Decimal = Decimal("2"),
        target_friction_mult: Decimal = Decimal("4"),
        stop_friction_mult: Decimal = Decimal("2"),
        max_holding_bars: int = 24,
        name: str = "triple_barrier",
    ) -> None:
        if lookback <= 1:
            raise ValueError("lookback must be greater than 1")
        if liquidity_bars <= 0:
            raise ValueError("liquidity_bars must be positive")
        if entry_friction_mult <= 0:
            raise ValueError("entry_friction_mult must be positive")
        if target_friction_mult <= 0:
            raise ValueError("target_friction_mult must be positive")
        if stop_friction_mult <= 0:
            raise ValueError("stop_friction_mult must be positive")
        if max_holding_bars <= 0:
            raise ValueError("max_holding_bars must be positive")

        self.name = name
        self.product_id = product_id
        self.granularity = granularity
        self.params: dict = {
            "granularity": granularity.value,
            "lookback": lookback,
            "liquidity_bars": liquidity_bars,
            "entry_friction_mult": entry_friction_mult,
            "target_friction_mult": target_friction_mult,
            "stop_friction_mult": stop_friction_mult,
            "max_holding_bars": max_holding_bars,
        }

    def param_space(self) -> tuple[ParamSpec, ...]:
        return (
            ParamSpec("entry_friction_mult", "decimal", 1.0, 4.0, Decimal("0.5")),
            ParamSpec("target_friction_mult", "decimal", 2.0, 8.0, Decimal("1")),
            ParamSpec("stop_friction_mult", "decimal", 1.0, 4.0, Decimal("0.5")),
            ParamSpec("max_holding_bars", "int", 6, 72, Decimal(6)),
        )

    def _decline(self, gate: str, **numbers: object) -> Setup | None:
        self.last_rejection = {"gate": gate, **numbers}
        return None

    def _series(self, candles_by_tf: dict[Granularity, list[Candle]]) -> list[Candle]:
        return candles_by_tf.get(self.granularity, [])

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """A CUSUM event, then barriers placed at multiples of this product's own round trip."""
        series = self._series(candles_by_tf)
        lookback = self.params["lookback"]
        liquidity_bars = self.params["liquidity_bars"]

        needed = max(lookback, liquidity_bars) + 1
        if len(series) < needed:
            return self._decline("insufficient_history", bars=len(series), bars_needed=needed)

        friction = per_product_round_trip(series[-liquidity_bars:], self.granularity)
        threshold = self.params["entry_friction_mult"] * friction
        reading = cusum_read([c.close for c in series[-lookback:]], threshold)
        event = {
            "s_plus": float(reading.s_plus),
            "threshold_pct": float(threshold),
            "round_trip_pct": float(friction),
        }
        if not reading.fired_up:
            return self._decline("cusum_threshold", **event)

        current = series[-1]
        entry = current.close
        target = entry * (1 + self.params["target_friction_mult"] * friction)
        stop = entry * (1 - self.params["stop_friction_mult"] * friction)
        # `0 < stop`, not merely `stop < entry`. A large enough `stop_friction_mult` drives
        # the barrier NEGATIVE, and a negative price still satisfies the ordering -- so an
        # ordering-only guard passes it, and `risk = entry - stop` then exceeds the entry
        # itself, which sizes a position off a loss larger than the whole holding. Caught by
        # the test written for a mutation that survived the first version of this line.
        if not 0 < stop < entry < target:
            return self._decline("barriers_degenerate", stop=float(stop), **event)

        self.last_rejection = None
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context={
                "rule_class": "event_gated",
                "round_trip_pct": float(friction),
                "target_friction_mult": self.params["target_friction_mult"],
                "stop_friction_mult": self.params["stop_friction_mult"],
                "max_holding_bars": self.params["max_holding_bars"],
                "s_plus": float(reading.s_plus),
            },
            ts=current.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """The VERTICAL barrier, and only that: has the position been held long enough?

        The horizontal barriers are `Setup.stop`/`Setup.target`, which the backtester and the
        account sim enforce -- restating them here would be two mechanisms deciding one exit.
        What no other rule has is this one: a holding-duration limit that closes at the bar's
        CLOSE regardless of price. That is executable under keel's market fills, which is why it
        is the leg of the source's method that transfers; a resting limit at a barrier is not.

        Counted in BARS ELAPSED SINCE `held.ts`, not in wall-clock time. The series is a rolling
        prefix in the backtest and the live cycle alike, so counting bars whose timestamp is
        after the entry's is the same question in both -- and it stays correct across a gap in
        the candle history, where a wall-clock subtraction would silently exit early.
        """
        series = self._series(candles_by_tf)
        if not series:
            return False
        elapsed = sum(1 for candle in series if candle.ts > held.ts)
        return elapsed >= self.params["max_holding_bars"]

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }
