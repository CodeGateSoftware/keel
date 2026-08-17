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

**This is a DAILY rule BY DEFAULT** (`self.granularity = Granularity.ONE_DAY`, a constructor
param persisted in `params` and read by the evaluation engine via `getattr(rule,
"granularity")` -- see `strategy.engine._trading_granularity`). The classic Turtle system is
a daily trend-follower, and that is not cosmetic: ADX is a *daily-scale* trend measure. On
noisy hourly bars +DI/-DI cancel out and ADX stays structurally suppressed (measured ~7 median
/ 18 max on real hourly BTC/ETH regardless of period), so an ADX>25 gate on hourly data never
fires and the rule produces zero trades. On daily candles ADX(14) has a ~26 median and
Donchian-high breakouts routinely coincide with ADX>25. So the lookback defaults are DAY counts.

**The `granularity` param exists to make the rule MEASURABLE on another clock, not profitable
on one (issue #337).** The hourly evidence profile (`config.paper-hourly.yaml`) runs the SAME
rule at `granularity=ONE_HOUR`, where the 40/20/14/20 defaults become BAR counts on hourly
candles: measured at n≈250 trades per 5 years (vs 4-13 daily) and NET-NEGATIVE -- 0 of 90 /
0 of 82 cells at every reachable fee (docs/experiments/2026-08-13). That profile collects
admissible forward evidence (rail vetoes, outcomes, pending lifespans) at a rate the daily
clock cannot supply; the param defaults to `ONE_DAY` so every row written before it existed
keeps meaning exactly what it meant. It follows `RsiMeanReversion.timeframe`'s persisted-param
convention, not `PullbackContinuation`'s non-persisted one (which `keel rules add` refuses
rather than silently rebuilding the rule at the default on a different candle series).
The entry/exit default to **40/20** -- a longer channel than the classic Turtle System-1 20/10 --
chosen by **walk-forward out-of-sample validation** on cached 5yr BTC/ETH/PAXG (every entry
lookback longer than 20 beat 20 out-of-sample; 40 was the most robust across held-out years). The
2:1 asymmetric ratio, ADX(14) gate, and 20-day ATR "N" are unchanged.

**Forming-bar lookahead guard.** Three callers present the daily series differently:
- The *edge* backtester (`strategy.backtest.backtest`) drives the rule on its native series
  only -- no `ONE_HOUR` key -- and every daily bar in it is already closed.
- The *account* simulator (`sim.portfolio_sim`) iterates hourly and hands the rule BOTH an
  `ONE_HOUR` window AND the `ONE_DAY` series, where the last daily bar is the CURRENT, still-
  forming day (its DB OHLC is the completed day = lookahead if consumed intraday).
- The *live agent* (`agent.run_once`) also passes an `ONE_HOUR` key, but `data.market_feed`
  persists only CLOSED candles, so its last daily bar has already closed.
`detect()`/`exit_signal()` therefore drop the last daily bar only when it is genuinely still
forming -- decided by where the hourly series SITS, not by whether it exists (`_completed_days`).
Keying it on the mere presence of `ONE_HOUR` cost the live agent a full day of lag on every
breakout, since it discarded a completed day the sim never had.
"""

from __future__ import annotations

from decimal import Decimal

from keel.analysis.indicators import adx, atr, donchian_high, donchian_low, macd
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

_DAY_SECONDS = 24 * 60 * 60


def _gap_pct(close: float, entry_level: float) -> float | None:
    """How far below the channel top `close` sits, in percent (negative once it has cleared it).

    `None` for a non-positive close rather than dividing by it -- a diagnostic must never be the
    thing that raises inside `detect()`.
    """
    if close <= 0:
        return None
    return (entry_level - close) / close * 100


def _completed_days(candles_by_tf: dict[Granularity, list[Candle]]) -> list[Candle]:
    """The `ONE_DAY` series with any still-FORMING last bar dropped.

    The account sim (`sim.portfolio_sim`) slices its daily series with
    `bisect_right(daily_ts, t)` against the current hourly bar `t`, so its last daily bar
    always CONTAINS that hourly bar -- it is the current, still-forming day, and its DB OHLC is
    the completed day (lookahead if consumed intraday).

    The live agent (`agent.run_once`) passes an `ONE_HOUR` key too, but `data.market_feed`
    persists only CLOSED candles, so its last daily bar has already closed. Keying the drop on
    the mere PRESENCE of `ONE_HOUR` therefore threw away a completed day there and left the
    rule deciding on a bar up to 48h old -- a day of lag on every breakout.

    So the test is where the hourly series SITS, not whether it exists: the newest daily bar is
    still forming unless the newest hourly bar opens at or after that day's close. In the sim
    that is never true (its hourly bar is by construction inside the day), so the sim's guard is
    unchanged; in the live agent it is true from the first full hour of the next UTC day.
    """
    daily = candles_by_tf.get(Granularity.ONE_DAY, [])
    hourly = candles_by_tf.get(Granularity.ONE_HOUR)
    if not hourly or not daily:
        return daily
    if hourly[-1].ts < daily[-1].ts + _DAY_SECONDS:
        return daily[:-1]
    return daily


class TurtleBreakout(Rule):
    """Donchian-breakout trend-follower: ADX-gated entry, asymmetric channel exit, 2xATR stop.

    One instance trades a single `product_id` on the candles of its declared `granularity`
    (default `ONE_DAY`; a persisted param -- see the module docstring for why it exists and
    why the default is daily).

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
        # Trading timeframe; the lookbacks below are BAR counts at THIS granularity, so the
        # daily defaults mean what they always meant and an hourly row reinterprets them as
        # hours -- the hourly corpus's exact configuration (docs/experiments/2026-08-11 §2).
        granularity: Granularity = Granularity.ONE_DAY,
        entry_lookback: int = 40,  # Donchian-high entry (days); walk-forward OOS default (was 20)
        exit_lookback: int = 20,  # Donchian-low asymmetric exit (days); half the entry (was 10)
        adx_period: int = 14,  # 14 days -- classic ADX
        adx_threshold: float = 25.0,  # ADX>25 trend confirmation (KB §25.1)
        atr_period: int = 20,  # 20 days = Turtle's "N"
        atr_stop_mult: Decimal = Decimal("2"),  # 2N stop (KB -- fixes 'stops too tight for crypto')
        use_macd_confirm: bool = False,  # optional MACD histogram>0 filter
        s1_filter: bool = False,  # Turtle S1 profitable-trade filter (default off); see detect()
        min_volume_filter: bool = False,  # require above-average breakout volume (default off)
        volume_ma_period: int = 20,  # lookback for the average-volume comparison (days)
        volume_mult: float = 1.2,  # breakout volume must exceed volume_mult x the average
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
        if volume_ma_period <= 0:
            raise ValueError("volume_ma_period must be positive")

        self.name = name
        self.product_id = product_id
        self.granularity = granularity
        self.params: dict = {
            "granularity": granularity.value,
            "entry_lookback": entry_lookback,
            "exit_lookback": exit_lookback,
            "adx_period": adx_period,
            "adx_threshold": adx_threshold,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "use_macd_confirm": use_macd_confirm,
            "s1_filter": s1_filter,
            "min_volume_filter": min_volume_filter,
            "volume_ma_period": volume_ma_period,
            "volume_mult": volume_mult,
            "target_rr": target_rr,
        }
        # memoizes the S1-filter decision by the completed-history's last ts, so the account
        # sim's repeated intraday calls on the same forming day don't re-replay (only ever
        # computed on a bar that actually breaks out, so this is cheap regardless).
        self._filter_cache: tuple[int, bool] | None = None

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def _decline(self, gate: str, **numbers: object) -> Setup | None:
        """Record WHY this bar declined on `last_rejection`, and return `None` for `detect()`.

        Returning `None` (rather than setting and letting the caller `return None`) keeps each
        decline a single line, so the reason can never drift from the branch that produced it.

        The return type is `Setup | None` rather than the `None` this always returns, because
        all 8 call sites are `return self._decline(...)` and mypy rejects using the value of a
        `-> None` call at all (`func-returns-value`) -- the annotation names what the CALLER
        returns, which is what makes the one-line idiom above type-check. It never returns a
        `Setup`; declining is the only thing it does.

        This only ever writes an attribute -- it does NOT log. `detect()` runs once per bar in
        `strategy.backtest` and `sim.portfolio_sim`, so logging here would emit millions of
        events in a sim; `engine.evaluate` reads the attribute and folds it into the one
        `engine.no_signal` event it already emits per declining rule.
        """
        self.last_rejection = {"gate": gate, **numbers}
        return None

    def _trading_series(
        self, candles_by_tf: dict[Granularity, list[Candle]]
    ) -> list[Candle]:
        """The series this rule decides on: `candles_by_tf[self.granularity]`.

        At the `ONE_DAY` default that is `_completed_days`'s forming-bar-guarded daily series,
        exactly as it always was. At any other declared granularity it is that series verbatim
        (no forming-day guard applies): the agent's `data.market_feed` persists only CLOSED
        candles, and the account sim presents the current bar as decided-on at its close --
        the same contract `PullbackContinuation`/`RsiMeanReversion` already trade under. An
        absent key yields an empty series, which `detect()` declines as insufficient history
        rather than silently falling back to some other granularity's bars -- a rule configured
        for hourly must never quietly decide on daily candles.
        """
        if self.granularity is Granularity.ONE_DAY:
            return _completed_days(candles_by_tf)
        return candles_by_tf.get(self.granularity, [])

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        """Confirmed close above the prior entry-lookback Donchian high, ADX>threshold, and
        (optionally) a positive MACD histogram -- then a 2xATR stop and a distant nominal
        target (the real exit is `exit_signal()`'s channel-low / the backtester's stop, not
        this target; it only exists to clear the evaluation engine's rr>=1 kill-zone gate and
        let winners run past a fixed 1:1/2:1 cap).
        """
        # The declared granularity's series; at the ONE_DAY default this drops a genuinely
        # still-forming last bar -- see `_completed_days`/`_trading_series`.
        daily = self._trading_series(candles_by_tf)

        entry_lookback = self.params["entry_lookback"]
        exit_lookback = self.params["exit_lookback"]
        adx_period = self.params["adx_period"]
        atr_period = self.params["atr_period"]

        min_needed = max(entry_lookback, adx_period, atr_period) + 2
        if len(daily) < min_needed:
            return self._decline("insufficient_history", bars=len(daily), bars_needed=min_needed)

        # PERFORMANCE: this rule runs every bar over long series (O(N^2) in a naive sim).
        # Only the current-bar indicator value is ever used, so bound the work: Donchian is
        # exact over its own window regardless of slice size; ATR/ADX are Wilder-smoothed and
        # converge, so a 4*period tail gives an accurate current value without recomputing the
        # full history each bar.
        needed = max(entry_lookback + 1, exit_lookback + 1, adx_period * 4, atr_period * 4)
        work = daily[-needed:]
        current = work[-1]

        entry_level = donchian_high(daily[:-1], entry_lookback)
        close = float(current.close)
        # Carried on EVERY decline from here down: that price cleared the channel and something
        # else declined it is the interesting case, and it is invisible from `signals=0` alone.
        breakout = {
            "close": close,
            "entry_level": entry_level,
            "gap_pct": _gap_pct(close, entry_level),
        }
        if not close > entry_level:
            return self._decline("donchian_high", **breakout)

        adx_now = adx(work, adx_period)[-1]
        if not adx_now > self.params["adx_threshold"]:
            return self._decline(
                "adx", adx=adx_now, adx_threshold=self.params["adx_threshold"], **breakout
            )

        if self.params["use_macd_confirm"]:
            closes = [float(c.close) for c in work]
            histogram = macd(closes)[2]
            if not histogram[-1] > 0:
                return self._decline("macd_histogram", histogram=histogram[-1], **breakout)

        # Low-volume breakout filter (default off): a breakout on thin volume is a likely
        # fakeout, so require the breakout bar's volume to exceed `volume_mult` x the average
        # volume of the prior `volume_ma_period` completed days (KB §54.23). Skips the entry
        # otherwise. Insufficient volume history -> not applied (allow).
        if self.params["min_volume_filter"]:
            vperiod = self.params["volume_ma_period"]
            prior = daily[-vperiod - 1 : -1]
            if prior:
                avg_vol = sum((c.volume for c in prior), Decimal(0)) / len(prior)
                required = Decimal(str(self.params["volume_mult"])) * avg_vol
                if not current.volume > required:
                    return self._decline(
                        "min_volume",
                        volume=float(current.volume),
                        volume_required=float(required),
                        **breakout,
                    )

        atr_now = Decimal(str(atr(work, atr_period)[-1]))
        if atr_now <= 0:
            return self._decline("atr", atr=float(atr_now), **breakout)

        entry = current.close
        stop = entry - self.params["atr_stop_mult"] * atr_now
        if stop >= entry:
            return self._decline("stop_not_below_entry", stop=float(stop), **breakout)

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
            return self._decline("s1_filter", **breakout)

        self.last_rejection = None  # this bar FIRED -- a stale reason would misreport it
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
        # Same declared-granularity series (and, at the ONE_DAY default, forming-bar guard)
        # as detect() -- see `_trading_series`/`_completed_days`.
        daily = self._trading_series(candles_by_tf)

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
        # (entry, stop) as ONE optional pair rather than two independently-optional locals:
        # they are set together on entry and cleared together on exit, and every read of the
        # stop happens on a bar where the entry is also live. As two variables that coupling
        # was invisible -- narrowing `pos_entry is None` told the checker nothing about
        # `pos_stop`, so the protective-stop comparison below read a `Decimal | None`. Pairing
        # them makes "stopped without an entry" unrepresentable instead of merely unreachable.
        position: tuple[Decimal, Decimal] | None = None
        for i in range(warmup, len(tail)):
            c = tail[i]
            if position is None:
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
                position = (entry_px, stop_px)
            else:
                # manage: 2N stop first (protective), then the asymmetric channel-low exit.
                pos_entry, pos_stop = position
                if c.low <= pos_stop:
                    won = False  # stopped out below entry
                    position = None
                elif float(c.close) <= donchian_low(tail[:i], exit_lookback):
                    won = c.close > pos_entry
                    position = None

        self._filter_cache = (last_ts, won)
        return won
