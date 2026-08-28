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
from typing import ClassVar, Literal

from keel.analysis.indicators import atr, rsi, rsi_divergence
from keel.analysis.levels import (
    MIN_TOUCH_SEPARATION_SEC,
    Level,
    find_levels,
    nearest_level,
)
from keel.strategy.rules.base import Rule, Setup
from keel.types import Candle, Granularity

StopMethod = Literal["fixed", "atr"]
TargetMethod = Literal["nearest_resistance", "fixed_rr"]


@dataclass
class RsiMeanReversion(Rule):
    """RSI mean-reversion: oversold bounce at support -> long entry; overbought ->
    exit a held long. See module docstring for the exact gating logic.
    """

    # Per-parameter docstrings, AT THE CLASS (issue #390 C4 / PRD O8): the single source
    # `keel.commands.rules.describe_params` renders by introspection. `ClassVar` is what
    # keeps the dataclass machinery from mistaking it for a constructor field -- an honest
    # annotation for a class-level table either way.
    PARAM_DOCS: ClassVar[dict[str, str]] = {
        "oversold": (
            "RSI level that marks oversold; the bounce trigger. Lower = fewer, deeper-dip entries."
        ),
        "overbought": "RSI level that closes a held long. Higher = hold winners longer.",
        "require_divergence": (
            "also require a bullish RSI divergence (price lower low, RSI higher low) at "
            "the bounce (default off)."
        ),
        "stop_method": (
            "how the stop is computed: 'fixed' (a percent below entry) or 'atr' (a "
            "multiple of ATR)."
        ),
        "target_method": "how the target is computed: 'nearest_resistance' or 'fixed_rr'.",
        "rsi_period": "RSI length (classic 14).",
        "atr_period": "ATR length for the 'atr' stop method.",
        "atr_mult": "ATR multiple for the 'atr' stop method. Wider = fewer stop-outs.",
        "fixed_stop_pct": "fraction below entry for the 'fixed' stop method (0.03 = 3%).",
        "fixed_rr": "reward:risk multiple for the 'fixed_rr' target method.",
        "trail_atr_mult": (
            "ratchet-only trailing stop this many ATRs below each bar's close, once the "
            "trade is open. Default off: measured WORSE than the static exit at the 120 bp "
            "fee (docs/experiments/2026-08-22-trailing-vs-static-exits.md). Applied by the "
            "sim/backtest engines and, since #502, by the agent's live per-cycle stop "
            "management -- off until a rule opts in."
        ),
        "be_roll_rr": (
            "roll the stop to the entry once the trade has been up this many R. Default "
            "off: measured no-better than the static exit at the 120 bp fee "
            "(docs/experiments/2026-08-22-trailing-vs-static-exits.md). Applied by the "
            "sim/backtest engines and, since #502, by the agent's live per-cycle stop "
            "management -- off until a rule opts in."
        ),
        "level_tolerance": (
            "how close two prices must be to count as one support/resistance level."
        ),
        "level_min_touches": (
            "touches required before a price counts as a support/resistance level."
        ),
        "level_min_separation_sec": (
            "minimum seconds between touches for them to count separately (pivots "
            "inside one consolidation are one visit, KB §81.5)."
        ),
        "support_proximity_pct": (
            "how close the bar's low must be to the support level, as a fraction of "
            "the level's price."
        ),
        "divergence_lookback": "bars scanned for the RSI divergence check.",
        "timeframe": "the candle series the rule decides on (ONE_HOUR default).",
    }

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
    # #442: the per-family exit-policy knobs `strategy.exit_policy` reads. Default OFF
    # (None) -- the wiring changes no rule's behavior until an operator/research run turns
    # a knob on. Honored by the sim/backtest engines and by the live cycle's management
    # step since #502 stage 2.
    trail_atr_mult: Decimal | None = None
    be_roll_rr: Decimal | None = None
    level_tolerance: Decimal = Decimal("0.002")
    level_min_touches: int = 3
    # KB §81.5: pivots inside one consolidation are one visit to a level, not several.
    level_min_separation_sec: int = MIN_TOUCH_SEPARATION_SEC
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
            "trail_atr_mult": self.trail_atr_mult,
            "be_roll_rr": self.be_roll_rr,
            "level_tolerance": self.level_tolerance,
            "level_min_touches": self.level_min_touches,
            "level_min_separation_sec": self.level_min_separation_sec,
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
            candles[:-1],
            tolerance=self.level_tolerance,
            min_touches=self.level_min_touches,
            min_separation_sec=self.level_min_separation_sec,
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
