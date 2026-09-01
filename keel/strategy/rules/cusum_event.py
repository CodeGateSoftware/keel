"""CUSUM event-driven entry gating (#341).

**The idea, and what makes it different from every other rule here.** The three shipped signal
rules evaluate a condition on EVERY bar and enter whenever it holds. This one asks a prior
question: has price moved far enough since the last event to be worth evaluating at all? A
symmetric CUSUM filter accumulates returns from a rolling anchor and fires only when the running
sum crosses a threshold, resetting that side when it does. Bars where nothing much happened do
not produce a decision, so the rule trades on EVENTS rather than on a clock.

Source: Grądzki et al., *Financial Innovation*, 2025-12-15
(https://jfin-swufe.springeropen.com/articles/10.1186/s40854-025-00866-w) -- BTC+ETH,
walk-forward, 2,700 runs disclosed; CUSUM sampling with wide barriers beat next-bar labeling,
with "excessive trading incurs a lot of costs" as the stated mechanism. **NOT independently
replicated**, and its fees were 0.1% per leg -- about twelve times lighter than what this
account pays.

⚠️ **THE HONEST PRIOR, WHICH IS THAT THIS WILL JOIN THE NULL.** The shipped rule library has
been measured to exhaustion (`docs/experiments/2026-08-13-restated-under-a-production-faithful-
engine.md`): zero of ninety asset-rule-parameter combinations are simultaneously measurable
(n>=100), gross-positive and net-positive at any fee this venue offers. Nothing here is expected
to change that, and this rule exists to be MEASURED rather than to be believed. Its own
mechanism cuts trade count, and the ρ=−0.77 bind between edge and sample size in
`2026-08-12-fee-curve-and-rsi-meanrev.md` says the rules with enough trades to promote are the
ones without edge. A gate that trades less is walking straight into that.

**THE THRESHOLD IS A MULTIPLE OF FRICTION, NOT A PERCENTAGE, and that is the whole design ask
of #341.** The source's 2.0-2.5% threshold is not conservative here -- it is almost exactly one
round trip on this venue (2 x 1.2% taker + 2 x 0.05% slippage = 2.5%), so a "2.5% move" event
names a move that pays for the trade and nothing more. Expressing the knob as
`threshold_friction_mult` makes that visible in the parameter itself: `1.0` reproduces the
paper's setting AND says out loud that it is break-even before the trade is even placed. The
default is `2` -- price must move twice what the round trip costs before an entry is considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from keel.analysis.indicators import atr
from keel.strategy.backtest import SLIPPAGE_FLOOR_PCT, TAKER_FEE_PCT
from keel.strategy.rules.base import ParamSpec, Rule, Setup
from keel.types import Candle, Granularity

#: What one round trip costs at this venue: two taker legs plus two slippage legs. Imported from
#: `strategy.backtest` rather than restated, so a rule whose threshold is DEFINED as a multiple
#: of friction cannot drift away from the number the backtest charges it.
ROUND_TRIP_FRICTION_PCT = 2 * TAKER_FEE_PCT + 2 * SLIPPAGE_FLOOR_PCT


@dataclass(frozen=True)
class CusumReading:
    """What the filter says about the LAST bar of a window."""

    #: Whether the upward sum crossed the threshold on the final bar -- the entry event.
    fired_up: bool
    #: Whether the downward sum crossed on the final bar -- the exit event.
    fired_down: bool
    #: The sums as they stand after the final bar, for the decline diagnostic. Both are
    #: post-reset: a side that just fired reads zero, which is the honest number -- the anchor
    #: has moved.
    s_plus: Decimal
    s_minus: Decimal


def cusum_read(closes: list[Decimal], threshold: Decimal) -> CusumReading:
    """Run the symmetric CUSUM filter over `closes` and report the final bar.

    `S+ = max(0, S+ + r)`, `S- = min(0, S- + r)` over simple returns -- **and the crossing side
    RESETS TO ZERO when it fires**, which is the part that makes this an event filter rather
    than a trend detector.

    That reset is not a detail. Without it `S+` climbs monotonically through a trending window
    and stays above the threshold for every subsequent bar, so a rule reading "is `S+` above
    the threshold" would fire on EVERY bar of the move -- exactly the every-bar evaluation this
    rule exists to replace, wearing a threshold. What the filter actually says is "an event
    happened HERE", once, after which the anchor moves to the current price and the next move
    is measured from there.

    Both sides are tracked even though keel can only act on the upward one: a one-sided filter
    accumulates an unbounded downward sum that never resets and then mis-times the next upward
    event.

    Pure and stateless. `detect()` is required to be pure, so the filter cannot live on the
    instance and is replayed over a bounded window every call -- see `CusumEvent.detect`.
    """
    s_plus = Decimal("0")
    s_minus = Decimal("0")
    fired_up = False
    fired_down = False
    for index, (previous, current) in enumerate(zip(closes, closes[1:])):
        last = index == len(closes) - 2
        if previous <= 0:
            continue
        change = (current - previous) / previous
        s_plus = max(Decimal("0"), s_plus + change)
        s_minus = min(Decimal("0"), s_minus + change)
        fired_up = s_plus >= threshold
        fired_down = s_minus <= -threshold
        if fired_up:
            s_plus = Decimal("0")
        if fired_down:
            s_minus = Decimal("0")
        if not last:
            # Only the FINAL bar's crossing is an event this call may act on. An earlier
            # crossing has already moved the anchor, which the reset above records; carrying
            # its flag forward would report a stale event on every later bar.
            fired_up = False
            fired_down = False
    return CusumReading(
        fired_up=fired_up, fired_down=fired_down, s_plus=s_plus, s_minus=s_minus
    )


class CusumEvent(Rule):
    """Enter long when the upward CUSUM sum crosses a friction-scaled threshold.

    `promotion_class` stays `"default"`: this is an entry FILTER over ordinary momentum, not a
    trend-follower, and claiming the low-win/high-R:R floor would hand it a gentler admission
    bar than its own mechanism earns.
    """

    decimal_params = ("threshold_friction_mult", "atr_stop_mult", "target_rr")
    granularity_param = "granularity"

    PARAM_DOCS = {
        "granularity": "Bar size the filter accumulates over.",
        "lookback": (
            "Bars the filter is replayed across each call. Bounds the work and, because the "
            "filter restarts at zero, DEFINES the state -- it is not a performance knob."
        ),
        "threshold_friction_mult": (
            "Event threshold as a multiple of one round trip (2 taker + 2 slippage legs). 1.0 "
            "is the source's own setting and is exactly break-even before the trade is placed."
        ),
        "atr_period": "ATR length the stop is sized from.",
        "atr_stop_mult": "Stop distance in ATRs below the entry.",
        "target_rr": "Nominal take-profit as a multiple of the stop distance.",
    }

    def __init__(
        self,
        product_id: str,
        granularity: Granularity = Granularity.ONE_HOUR,
        lookback: int = 168,
        threshold_friction_mult: Decimal = Decimal("2"),
        atr_period: int = 20,
        atr_stop_mult: Decimal = Decimal("2"),
        target_rr: Decimal = Decimal("3"),
        name: str = "cusum_event",
    ) -> None:
        if lookback <= 1:
            raise ValueError("lookback must be greater than 1 -- a filter needs a return to sum")
        if threshold_friction_mult <= 0:
            raise ValueError("threshold_friction_mult must be positive")
        if atr_period <= 0:
            raise ValueError("atr_period must be positive")
        if atr_stop_mult <= 0:
            raise ValueError("atr_stop_mult must be positive")
        if target_rr <= 0:
            raise ValueError("target_rr must be positive")

        self.name = name
        self.product_id = product_id
        self.granularity = granularity
        self.params: dict = {
            "granularity": granularity.value,
            "lookback": lookback,
            "threshold_friction_mult": threshold_friction_mult,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "target_rr": target_rr,
        }

    @property
    def threshold_pct(self) -> Decimal:
        """The event threshold as a fraction of price -- the friction multiple, made concrete.

        A property rather than a stored param so the two can never disagree: the stored knob is
        the MULTIPLE, and the percentage it implies is derived from the same constants the
        backtest charges. A rule that persisted the percentage would keep answering 2.5% after
        a fee change that made 2.5% mean something else.
        """
        return self.params["threshold_friction_mult"] * ROUND_TRIP_FRICTION_PCT

    def param_space(self) -> tuple[ParamSpec, ...]:
        return (
            ParamSpec("threshold_friction_mult", "decimal", 1.0, 4.0, Decimal("0.5")),
            ParamSpec("lookback", "int", 48, 336, Decimal(48)),
            ParamSpec("atr_stop_mult", "decimal", 1.5, 3.0, Decimal("0.5")),
            ParamSpec("target_rr", "decimal", 2.0, 6.0, Decimal("1")),
        )

    def _decline(self, gate: str, **numbers: object) -> Setup | None:
        """Record WHY this bar declined, and return `None` for `detect()`. Never logs -- see
        `TurtleBreakout._decline` for the reasoning (this runs once per bar in a sim)."""
        self.last_rejection = {"gate": gate, **numbers}
        return None

    def _series(self, candles_by_tf: dict[Granularity, list[Candle]]) -> list[Candle]:
        """The declared granularity's series, or empty. An absent key declines as insufficient
        history rather than falling back -- a rule configured for hourly must never quietly
        decide on daily bars."""
        return candles_by_tf.get(self.granularity, [])

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """An upward CUSUM event, then an ATR stop and a nominal target.

        Pure: the filter is replayed from zero over the last `lookback` bars every call, so the
        same candles always produce the same answer whether this is the live cycle, the edge
        backtest or the account sim.
        """
        series = self._series(candles_by_tf)
        lookback = self.params["lookback"]
        atr_period = self.params["atr_period"]

        needed = max(lookback, atr_period * 4) + 1
        if len(series) < needed:
            return self._decline("insufficient_history", bars=len(series), bars_needed=needed)

        threshold = self.threshold_pct
        reading = cusum_read([c.close for c in series[-lookback:]], threshold)
        # Carried on every decline from here down: how far the sum sat from firing is the whole
        # diagnostic value of an event filter, and it is invisible from `signals=0` alone.
        event = {
            "s_plus": float(reading.s_plus),
            "s_minus": float(reading.s_minus),
            "threshold_pct": float(threshold),
            "friction_mult": float(self.params["threshold_friction_mult"]),
        }
        if not reading.fired_up:
            return self._decline("cusum_threshold", **event)

        work = series[-(atr_period * 4) :]
        atr_now = Decimal(str(atr(work, atr_period)[-1]))
        if atr_now <= 0:
            return self._decline("atr", atr=float(atr_now), **event)

        current = series[-1]
        entry = current.close
        stop = entry - self.params["atr_stop_mult"] * atr_now
        if stop >= entry:
            return self._decline("stop_not_below_entry", stop=float(stop), **event)

        risk = entry - stop
        target = entry + self.params["target_rr"] * risk

        self.last_rejection = None  # this bar FIRED -- a stale reason would misreport it
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context={
                "rule_class": "event_gated",
                "s_plus_before_reset": float(threshold),
                "threshold_pct": float(threshold),
                "friction_mult": float(self.params["threshold_friction_mult"]),
                "atr": float(atr_now),
                "atr_stop_mult": self.params["atr_stop_mult"],
                "lookback": lookback,
            },
            ts=current.ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        """The same filter, the other side: a downward event of equal size closes the position.

        Symmetric on purpose. The entry's claim is that a move of this size is the smallest one
        worth paying for; the identical claim in reverse is the smallest one worth exiting on,
        and a different exit threshold would be a second free parameter with no evidence behind
        it. The triple-barrier exits the source pairs CUSUM with are #342's, deliberately not
        smuggled in here -- this rule must be measurable on its own before it is combined.

        `held` is unused: the stop and the nominal target are the backtester's and the account
        sim's to enforce, exactly as for `TurtleBreakout`.
        """
        del held
        series = self._series(candles_by_tf)
        lookback = self.params["lookback"]
        if len(series) < lookback + 1:
            return False
        return cusum_read([c.close for c in series[-lookback:]], self.threshold_pct).fired_down

    def describe(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "param_space": [spec.plain() for spec in self.param_space()],
        }
