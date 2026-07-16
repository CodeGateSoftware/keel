"""RSI mean-reversion rule (long-only).

`detect()`: an RSI oversold bounce (RSI dipped below `oversold` on the prior bar and
is now recovering) occurring at a known support level (KB source-01 §1.3 — a
clustered, multi-touch S/R level, not just any local low) triggers a long `Setup`.
When `require_divergence=True`, a bullish RSI divergence (price making a lower low
while RSI makes a higher low, KB source-01 §1.4) is also required.

`exit_signal()`: RSI overbought closes a held long. Per the Global Constraints this
rule is **long-only** — overbought is an exit/don't-buy signal only, never a short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from halal_cb.analysis.indicators import atr, rsi, rsi_divergence
from halal_cb.analysis.levels import Level, find_levels, nearest_level
from halal_cb.strategy.rules.base import Rule, Setup
from halal_cb.types import Candle, Granularity

StopMethod = Literal["fixed", "atr"]
TargetMethod = Literal["nearest_resistance", "fixed_rr"]


@dataclass
class RsiMeanReversion(Rule):
    """RSI mean-reversion: oversold bounce at support -> long entry; overbought ->
    exit a held long. See module docstring for the exact gating logic.
    """

    oversold: float = 20.0
    overbought: float = 80.0
    require_divergence: bool = False
    stop_method: StopMethod = "atr"
    target_method: TargetMethod = "fixed_rr"
    rsi_period: int = 14
    atr_period: int = 14
    atr_mult: Decimal = Decimal("1.5")
    fixed_stop_pct: Decimal = Decimal("0.03")
    fixed_rr: Decimal = Decimal("2")
    level_tolerance: Decimal = Decimal("0.002")
    level_min_touches: int = 3
    support_proximity_pct: Decimal = Decimal("0.005")
    divergence_lookback: int = 20
    timeframe: Granularity = Granularity.ONE_HOUR
    product_id: str = "BTC-USD"

    name: str = field(default="rsi_meanrev", init=False)
    params: dict = field(init=False)

    def __post_init__(self) -> None:
        self.params = {
            "oversold": self.oversold,
            "overbought": self.overbought,
            "require_divergence": self.require_divergence,
            "stop_method": self.stop_method,
            "target_method": self.target_method,
            "rsi_period": self.rsi_period,
            "atr_period": self.atr_period,
            "atr_mult": self.atr_mult,
            "fixed_stop_pct": self.fixed_stop_pct,
            "fixed_rr": self.fixed_rr,
            "level_tolerance": self.level_tolerance,
            "level_min_touches": self.level_min_touches,
            "support_proximity_pct": self.support_proximity_pct,
            "divergence_lookback": self.divergence_lookback,
            "timeframe": self.timeframe.value,
            "product_id": self.product_id,
        }

    # ------------------------------------------------------------------
    # Rule interface
    # ------------------------------------------------------------------

    def detect(self, candles_by_tf: dict[Granularity, list[Candle]]) -> Setup | None:
        candles = candles_by_tf.get(self.timeframe)
        if not candles or len(candles) < self.rsi_period + 2:
            return None

        rsi_vals = rsi([float(c.close) for c in candles], period=self.rsi_period)
        prev_rsi, curr_rsi = rsi_vals[-2], rsi_vals[-1]

        if not (prev_rsi < self.oversold and curr_rsi > prev_rsi):
            return None

        levels = find_levels(
            candles[:-1], tolerance=self.level_tolerance, min_touches=self.level_min_touches
        )
        support = nearest_level(candles[-1].low, levels, kind="support")
        if support is None:
            return None
        if abs(candles[-1].low - support.price) > support.price * self.support_proximity_pct:
            return None

        divergence = rsi_divergence(candles, rsi_vals, lookback=self.divergence_lookback)
        if self.require_divergence and divergence != "bullish":
            return None

        entry = candles[-1].close
        stop = self._compute_stop(candles, entry)
        if stop >= entry:
            return None
        target = self._compute_target(entry, stop, levels)
        if target <= entry:
            return None

        context = {
            "rsi": curr_rsi,
            "prev_rsi": prev_rsi,
            "oversold": self.oversold,
            "support_price": support.price,
            "support_touches": support.touches,
            "divergence": divergence,
        }
        return Setup(
            product_id=self.product_id,
            direction="long",
            entry=entry,
            stop=stop,
            target=target,
            context=context,
            ts=candles[-1].ts,
        )

    def exit_signal(self, held: Setup, candles_by_tf: dict[Granularity, list[Candle]]) -> bool:
        candles = candles_by_tf.get(self.timeframe)
        if not candles:
            return False
        rsi_vals = rsi([float(c.close) for c in candles], period=self.rsi_period)
        return rsi_vals[-1] > self.overbought

    def describe(self) -> dict:
        return {"name": self.name, "params": self.params}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_stop(self, candles: list[Candle], entry: Decimal) -> Decimal:
        if self.stop_method == "fixed":
            return entry * (Decimal(1) - self.fixed_stop_pct)
        if self.stop_method == "atr":
            atr_vals = atr(candles, period=self.atr_period)
            atr_val = Decimal(str(atr_vals[-1]))
            return entry - atr_val * self.atr_mult
        raise ValueError(f"unknown stop_method: {self.stop_method!r}")

    def _compute_target(self, entry: Decimal, stop: Decimal, levels: list[Level]) -> Decimal:
        risk = entry - stop
        if self.target_method == "nearest_resistance":
            resistance = nearest_level(entry, levels, kind="resistance")
            if resistance is not None and resistance.price > entry:
                return resistance.price
            return entry + risk * self.fixed_rr
        if self.target_method == "fixed_rr":
            return entry + risk * self.fixed_rr
        raise ValueError(f"unknown target_method: {self.target_method!r}")
