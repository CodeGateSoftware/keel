"""Rule: long-only Turtle-style Donchian breakout (KB source-27 §27.1, source-25 §25.1).

The canonical, decades-validated Turtle Trading system (Dennis/Eckhardt), long-only: enter
on a confirmed close above the prior N-day Donchian high, gated by ADX>25 trend confirmation
(§25.1 -- "ADX<25 = ranging, stand aside", rejects false breakouts) and size/stop off ATR
("N" in Turtle parlance, §27.1 -- "Turtles used ~2N" for the stop). The exit channel is
deliberately **asymmetric**: a shorter Donchian-low lookback than the entry lookback (a 2:1
ratio; classic Turtle System-1 is 20/10, this rule defaults to a longer 40/20 -- see below) so
winners are given room to run on the way up but the trade is cut faster on the way down than the
entry signal took to fire.

Long-only spot, no shorts: this rule only ever emits a **long** `Setup` (a close above the
high channel); the opposite side (a close below the low channel) is exit-only, wired through
`exit_signal()`, never a short entry -- consistent with every other rule in this codebase.

**This is a DAILY rule** (`self.granularity = Granularity.ONE_DAY`, a fixed attribute read by
the evaluation engine via `getattr(rule, "granularity")` -- see
`strategy.engine._trading_granularity` -- not a persisted param). The classic Turtle system is
a daily trend-follower, and that is not cosmetic: ADX is a *daily-scale* trend measure. On
noisy hourly bars +DI/-DI cancel out and ADX stays structurally suppressed (measured ~7 median
/ 18 max on real hourly BTC/ETH regardless of period), so an ADX>25 gate on hourly data never
fires and the rule produces zero trades. On daily candles ADX(14) has a ~26 median and
Donchian-high breakouts routinely coincide with ADX>25. So the lookback defaults are DAY counts.
The entry/exit default to **40/20** -- a longer channel than the classic Turtle System-1 20/10 --
chosen by **walk-forward out-of-sample validation** on cached 5yr BTC/ETH/PAXG (every entry
lookback longer than 20 beat 20 out-of-sample; 40 was the most robust across held-out years). The
2:1 asymmetric ratio, ADX(14) gate, and 20-day ATR "N" are unchanged.

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

    # How many completed daily bars back the S1 filter replays to find the most recent
    # completed prior breakout trade (~16 months) -- generous enough to always contain it at
    # these channel lengths, and it only runs on a bar that breaks out, so the cost is trivial.
    _REPLAY_TAIL = 400

    def __init__(
        self,
        product_id: str,
        entry_lookback: int = 40,  # Donchian-high entry (days); walk-forward OOS default (was 20)
        exit_lookback: int = 20,  # Donchian-low asymmetric exit (days); half the entry (was 10)
        adx_period: int = 14,  # 14 days -- classic ADX
        adx_threshold: float = 25.0,  # ADX>25 trend confirmation (KB §25.1)
        atr_period: int = 20,  # 20 days = Turtle's "N"
        atr_stop_mult: Decimal = Decimal("2"),  # 2N stop (KB -- fixes 'stops too tight for crypto')
        use_macd_confirm: bool = False,  # optional MACD histogram>0 filter
        s1_filter: bool = False,  # Turtle S1 profitable-trade filter (default off); see detect()
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
            "s1_filter": s1_filter,
            "target_rr": target_rr,
        }
        # memoizes the S1-filter decision by the completed-history's last ts, so the account
        # sim's repeated intraday calls on the same forming day don't re-replay (only ever
        # computed on a bar that actually breaks out, so this is cheap regardless).
        self._filter_cache: tuple[int, bool] | None = None

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

        # Turtle S1 profitable-trade filter (default off): a winning breakout tends to be
        # followed by a false one, a losing (shakeout) breakout by the real trend. So skip this
        # entry if the most recent COMPLETED prior breakout trade would have won; take it only
        # after a loss. Computed purely from price history (the prior breakout's hypothetical
        # outcome "whether or not it was taken", per the canonical rule) -- no cross-loop state,
        # so it behaves identically in the edge backtest, the account sim, and live. Runs only
        # here, on a bar that actually breaks out, so the bounded replay is cheap.
        if self.params["s1_filter"] and self._prior_breakout_won(daily):
            return None

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

    # ------------------------------------------------------------------
    # S1 profitable-trade filter (pure history; only called from detect() on a breakout bar)
    # ------------------------------------------------------------------

    def _prior_breakout_won(self, daily: list[Candle]) -> bool:
        """Did the most recent COMPLETED prior breakout trade win?

        `daily` is the (forming-bar-guarded) completed-day series whose last bar is the CURRENT
        breakout being decided; we look strictly before it (`daily[:-1]`). Replays this rule's
        own unfiltered entry/exit sequentially over a bounded tail and returns whether the most
        recently completed hypothetical trade won (exit above entry). No prior completed trade ->
        `False` (don't skip). Memoized by the completed-history's last ts for the account sim's
        repeated intraday calls.
        """
        hist = daily[:-1]
        if not hist:
            return False
        last_ts = hist[-1].ts
        if self._filter_cache is not None and self._filter_cache[0] == last_ts:
            return self._filter_cache[1]

        entry_lookback = self.params["entry_lookback"]
        exit_lookback = self.params["exit_lookback"]
        adx_period = self.params["adx_period"]
        atr_period = self.params["atr_period"]
        adx_threshold = self.params["adx_threshold"]
        stop_mult = self.params["atr_stop_mult"]
        use_macd = self.params["use_macd_confirm"]

        tail = hist[-self._REPLAY_TAIL :]
        warmup = max(entry_lookback, adx_period, atr_period) + 1

        won = False
        pos_entry: Decimal | None = None
        pos_stop: Decimal | None = None
        for i in range(warmup, len(tail)):
            c = tail[i]
            if pos_entry is None:
                # entry mirrors detect(): close > prior-entry_lookback Donchian high, ADX>thr,
                # optional MACD>0, valid 2N stop.
                if not float(c.close) > donchian_high(tail[:i], entry_lookback):
                    continue
                work = tail[max(0, i - 4 * adx_period) : i + 1]
                if not adx(work, adx_period)[-1] > adx_threshold:
                    continue
                if use_macd:
                    closes = [float(x.close) for x in work]
                    if not macd(closes)[2][-1] > 0:
                        continue
                atr_work = tail[max(0, i - 4 * atr_period) : i + 1]
                atr_i = Decimal(str(atr(atr_work, atr_period)[-1]))
                if atr_i <= 0:
                    continue
                entry_px = c.close
                stop_px = entry_px - stop_mult * atr_i
                if stop_px >= entry_px:
                    continue
                pos_entry, pos_stop = entry_px, stop_px
            else:
                # manage: 2N stop first (protective), then the asymmetric channel-low exit.
                if c.low <= pos_stop:
                    won = False  # stopped out below entry
                    pos_entry = pos_stop = None
                elif float(c.close) <= donchian_low(tail[:i], exit_lookback):
                    won = c.close > pos_entry
                    pos_entry = pos_stop = None

        self._filter_cache = (last_ts, won)
        return won
