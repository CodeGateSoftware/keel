"""Rule: long-only Turtle-style Donchian breakout (KB source-27 §27.1, source-25 §25.1).

The canonical, decades-validated Turtle Trading system (Dennis/Eckhardt), long-only: enter
on a confirmed close above the prior N-day Donchian high, gated by ADX>25 trend confirmation
(§25.1 -- "ADX<25 = ranging, stand aside", rejects false breakouts) and size/stop off ATR
("N" in Turtle parlance, §27.1 -- "Turtles used ~2N" for the stop). The exit channel is
deliberately **asymmetric**: a shorter Donchian-low lookback than the entry lookback (Turtle
System-1 = 20/10) so winners are given room to run on the way up but the trade is cut faster
on the way down than the entry signal took to fire.

Long-only spot, no shorts: this rule only ever emits a **long** `Setup` (a close above the
high channel); the opposite side (a close below the low channel) is exit-only, wired through
`exit_signal()`, never a short entry -- consistent with every other rule in this codebase.

**This is a DAILY rule** (`self.granularity = Granularity.ONE_DAY`, a fixed attribute read by
the evaluation engine via `getattr(rule, "granularity")` -- see
`strategy.engine._trading_granularity` -- not a persisted param). The classic Turtle system is
a daily trend-follower, and that is not cosmetic: ADX is a *daily-scale* trend measure. On
noisy hourly bars +DI/-DI cancel out and ADX stays structurally suppressed (measured ~7 median
/ 18 max on real hourly BTC/ETH regardless of period), so an ADX>25 gate on hourly data never
fires and the rule produces zero trades. On daily candles ADX(14) has a ~26 median and 20-day-
high breakouts routinely coincide with ADX>25. So the lookback defaults are DAY counts (Turtle
System-1 20/10 + ADX(14) + 20-day ATR "N").

**Forming-bar lookahead guard.** The two backtest passes present the daily series differently:
- The *edge* backtester (`strategy.backtest.backtest`) drives the rule on its native series
  only -- no `ONE_HOUR` key -- and every daily bar in it is already closed.
- The *account* simulator (`sim.portfolio_sim`) iterates hourly and hands the rule BOTH an
  `ONE_HOUR` window AND the `ONE_DAY` series, where the last daily bar is the CURRENT, still-
  forming day (its DB OHLC is the completed day = lookahead if consumed intraday).
`detect()`/`exit_signal()` therefore drop the last daily bar iff an `ONE_HOUR` key is present,
so decisions are made only on completed days in the account pass while using every (closed) bar
in the edge pass.
"""

from __future__ import annotations

from decimal import Decimal

from keel.analysis.indicators import adx, atr, donchian_high, donchian_low, macd
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity


class TurtleBreakout(Rule):
    """Donchian-breakout trend-follower: ADX-gated entry, asymmetric channel exit, 2xATR stop.

    One instance trades a single `product_id` on `ONE_DAY` candles only (`self.granularity`,
    fixed -- not one of the tunable `params`).

    `promotion_class = "trend_follow"` routes it to the low-win/high-R:R promotion floor
    (`strategy.promotion.floor_for_class`): a breakout trend-follower wins under half its trades
    by design and would fail the global 55%-win floor despite a positive expectancy (KB §25.5).
    """

    promotion_class = "trend_follow"

    def __init__(
        self,
        product_id: str,
        entry_lookback: int = 20,  # 20 trading days (Donchian high, entry) -- Turtle System-1
        exit_lookback: int = 10,  # 10 days (Donchian low, asymmetric channel exit)
        adx_period: int = 14,  # 14 days -- classic ADX
        adx_threshold: float = 25.0,  # ADX>25 trend confirmation (KB §25.1)
        atr_period: int = 20,  # 20 days = Turtle's "N"
        atr_stop_mult: Decimal = Decimal("2"),  # 2N stop (KB -- fixes 'stops too tight for crypto')
        use_macd_confirm: bool = False,  # optional MACD histogram>0 filter
        target_rr: Decimal = Decimal("6"),  # distant nominal take-profit; see detect()
        name: str = "turtle_breakout",
    ) -> None:
        if entry_lookback <= 0:
            raise ValueError("entry_lookback must be positive")
        if exit_lookback <= 0:
            raise ValueError("exit_lookback must be positive")
        if adx_period <= 0:
            raise ValueError("adx_period must be positive")
        if atr_period <= 0:
            raise ValueError("atr_period must be positive")

        self.name = name
        self.product_id = product_id
        self.granularity = Granularity.ONE_DAY
        self.params: dict = {
            "entry_lookback": entry_lookback,
            "exit_lookback": exit_lookback,
            "adx_period": adx_period,
            "adx_threshold": adx_threshold,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "use_macd_confirm": use_macd_confirm,
            "target_rr": target_rr,
        }

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Confirmed close above the prior entry-lookback Donchian high, ADX>threshold, and
        (optionally) a positive MACD histogram -- then a 2xATR stop and a distant nominal
        target (the real exit is `exit_signal()`'s channel-low / the backtester's stop, not
        this target; it only exists to clear the evaluation engine's rr>=1 kill-zone gate and
        let winners run past a fixed 1:1/2:1 cap).
        """
        daily = candles_by_tf.get(Granularity.ONE_DAY, [])
        # Account sim (portfolio_sim) includes the current *forming* daily bar alongside an
        # ONE_HOUR window -> drop it to decide only on completed days (no lookahead). The
        # daily-only edge backtest has no ONE_HOUR key and every daily bar is already closed,
        # so use them all.
        if candles_by_tf.get(Granularity.ONE_HOUR):
            daily = daily[:-1]

        entry_lookback = self.params["entry_lookback"]
        exit_lookback = self.params["exit_lookback"]
        adx_period = self.params["adx_period"]
        atr_period = self.params["atr_period"]

        min_needed = max(entry_lookback, adx_period, atr_period) + 2
        if len(daily) < min_needed:
            return None

        # PERFORMANCE: this rule runs every bar over long series (O(N^2) in a naive sim).
        # Only the current-bar indicator value is ever used, so bound the work: Donchian is
        # exact over its own window regardless of slice size; ATR/ADX are Wilder-smoothed and
        # converge, so a 4*period tail gives an accurate current value without recomputing the
        # full history each bar.
        needed = max(entry_lookback + 1, exit_lookback + 1, adx_period * 4, atr_period * 4)
        work = daily[-needed:]
        current = work[-1]

        entry_level = donchian_high(daily[:-1], entry_lookback)
        if not float(current.close) > entry_level:
            return None

        adx_now = adx(work, adx_period)[-1]
        if not adx_now > self.params["adx_threshold"]:
            return None

        if self.params["use_macd_confirm"]:
            closes = [float(c.close) for c in work]
            histogram = macd(closes)[2]
            if not histogram[-1] > 0:
                return None

        atr_now = Decimal(str(atr(work, atr_period)[-1]))
        if atr_now <= 0:
            return None

        entry = current.close
        stop = entry - self.params["atr_stop_mult"] * atr_now
        if stop >= entry:
            return None

        risk = entry - stop
        target = entry + self.params["target_rr"] * risk

        context = {
            "rule_class": "trend_follow",
            "adx": adx_now,
            "entry_level": entry_level,
            "atr": float(atr_now),
            "atr_stop_mult": self.params["atr_stop_mult"],
            "donchian_entry": entry_lookback,
            "donchian_exit": exit_lookback,
        }
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context=context,
            ts=current.ts,
        )

    def exit_signal(
        self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> bool:
        """The asymmetric Turtle channel exit: a close at/below the prior exit-lookback
        Donchian low.

        `held` is accepted per the `Rule` interface but unused: this rule's channel exit
        doesn't reference the specific held setup's stop/target -- the 2N stop and the
        nominal target are the backtester/account-sim's job to enforce separately.
        """
        del held
        daily = candles_by_tf.get(Granularity.ONE_DAY, [])
        # Same forming-bar guard as detect(): the account sim carries the current forming daily
        # bar alongside an ONE_HOUR window -> decide on completed days only.
        if candles_by_tf.get(Granularity.ONE_HOUR):
            daily = daily[:-1]

        exit_lookback = self.params["exit_lookback"]
        if len(daily) <= exit_lookback + 1:
            return False

        exit_level = donchian_low(daily[:-1], exit_lookback)
        return float(daily[-1].close) <= exit_level

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}
