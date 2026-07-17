"""Technical indicators: EMA fan, RSI (+ divergence), MACD, ATR, Fibonacci, deceleration.

Per the Global Constraints, indicator math uses plain `float` (never `Decimal` — that's
reserved for money/prices elsewhere). The two exceptions are `fib_retracements` and
`fib_extensions`, which operate on and return `Decimal` prices since they're level math,
not oscillator math.

All EMA-family functions (`ema`, and anything built on it) use the ``adjust=False``
convention: the first output equals the first input, and every subsequent value is the
standard exponential blend `alpha*v + (1-alpha)*prev`. This keeps the output the same
length as the input (no leading `None`/`NaN`) and is easy to hand-verify.

No third-party numeric dependencies (no numpy/pandas) — everything below is hand-rolled.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from keel.types import Candle

Direction = Literal["bullish", "bearish"]

_RETRACEMENT_RATIOS = (
    Decimal("0.382"),
    Decimal("0.5"),
    Decimal("0.618"),
    Decimal("0.786"),
    Decimal("0.886"),
)
_EXTENSION_RATIOS = (Decimal("1.272"), Decimal("1.618"))


def _closes(candles: list[Candle]) -> list[float]:
    return [float(c.close) for c in candles]


# ---------------------------------------------------------------------------
# EMA / EMA fan
# ---------------------------------------------------------------------------


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average, `adjust=False` convention (seed = first value)."""
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out: list[float] = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * float(v) + (1 - alpha) * out[-1])
    return out


def ema_fan(
    candles: list[Candle], periods: tuple[int, ...] = (8, 20, 50)
) -> dict[int, list[float]]:
    """EMA of closes for each period in `periods`, keyed by period."""
    closes = _closes(candles)
    return {period: ema(closes, period) for period in periods}


def fan_aligned(fan: dict[int, list[float]], idx: int, direction: Direction) -> bool:
    """True if the EMAs are strictly stacked fast>mid>slow (bullish) or reversed (bearish).

    `fan` keys are periods; the fastest EMA is the smallest period.
    """
    periods = sorted(fan.keys())
    try:
        vals = [fan[p][idx] for p in periods]
    except IndexError:
        return False
    if direction == "bullish":
        return all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------


def rsi(values: list[float], period: int = 14) -> list[float]:
    """Wilder's RSI. Entries before `period` diffs are available default to 50.0 (neutral)."""
    n = len(values)
    result = [50.0] * n
    if n < period + 1:
        return result

    diffs = [float(values[i]) - float(values[i - 1]) for i in range(1, n)]
    gains = [d if d > 0 else 0.0 for d in diffs]
    losses = [-d if d < 0 else 0.0 for d in diffs]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi_value(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1 + rs)

    result[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, n):
        gain = gains[i - 1]
        loss = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = _rsi_value(avg_gain, avg_loss)
    return result


def is_overbought(rsi_val: float, thr: float = 80.0) -> bool:
    return rsi_val > thr


def is_oversold(rsi_val: float, thr: float = 20.0) -> bool:
    return rsi_val < thr


def _swing_highs(candles: list[Candle]) -> list[int]:
    """Indices of simple 3-bar price pivots (high above both neighbors)."""
    idxs = []
    for i in range(1, len(candles) - 1):
        if candles[i].high > candles[i - 1].high and candles[i].high > candles[i + 1].high:
            idxs.append(i)
    return idxs


def _swing_lows(candles: list[Candle]) -> list[int]:
    """Indices of simple 3-bar price pivots (low below both neighbors)."""
    idxs = []
    for i in range(1, len(candles) - 1):
        if candles[i].low < candles[i - 1].low and candles[i].low < candles[i + 1].low:
            idxs.append(i)
    return idxs


def rsi_divergence(
    candles: list[Candle], rsi_vals: list[float], lookback: int = 20
) -> Direction | None:
    """Bearish: price higher high, RSI lower high. Bullish: price lower low, RSI higher low.

    Compares the two most recent swing highs (for bearish) / swing lows (for bullish)
    within the last `lookback` candles. Returns None if no divergence or too little data.
    """
    n = len(candles)
    if n < 3 or len(rsi_vals) != n:
        return None

    start = max(0, n - lookback)
    offset = start

    highs_idx = [i + offset for i in _swing_highs(candles[start:n])]
    lows_idx = [i + offset for i in _swing_lows(candles[start:n])]

    if len(highs_idx) >= 2:
        prev_i, last_i = highs_idx[-2], highs_idx[-1]
        if candles[last_i].high > candles[prev_i].high and rsi_vals[last_i] < rsi_vals[prev_i]:
            return "bearish"

    if len(lows_idx) >= 2:
        prev_i, last_i = lows_idx[-2], lows_idx[-1]
        if candles[last_i].low < candles[prev_i].low and rsi_vals[last_i] > rsi_vals[prev_i]:
            return "bullish"

    return None


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]]:
    """MACD line (fast EMA - slow EMA), signal line (EMA of MACD line), histogram."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow, strict=True)]
    signal_line = ema(macd_line, signal)
    histogram = [m - s for m, s in zip(macd_line, signal_line, strict=True)]
    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------


def atr(candles: list[Candle], period: int = 14) -> list[float]:
    """Average True Range (Wilder smoothing), absolute price units.

    True range uses the prior close where available. Before enough true-range samples
    exist to seed the Wilder average, entries are back-filled with that seed value
    (avoids `NaN` while keeping the return type plain `float`).
    """
    n = len(candles)
    if n == 0:
        return []

    trs: list[float] = []
    for i, c in enumerate(candles):
        h, low = float(c.high), float(c.low)
        if i == 0:
            tr = h - low
        else:
            prev_close = float(candles[i - 1].close)
            tr = max(h - low, abs(h - prev_close), abs(low - prev_close))
        trs.append(tr)

    result = [0.0] * n
    seed_len = min(period, n)
    seed = sum(trs[:seed_len]) / seed_len
    for i in range(seed_len):
        result[i] = seed
    for i in range(seed_len, n):
        result[i] = (result[i - 1] * (period - 1) + trs[i]) / period
    return result


# ---------------------------------------------------------------------------
# Fibonacci retracement / extension
# ---------------------------------------------------------------------------


def fib_retracements(swing_high: Decimal, swing_low: Decimal) -> dict[str, Decimal]:
    """Retracement levels (0.382/0.5/0.618/0.786/0.886) pulling back from `swing_high`."""
    diff = swing_high - swing_low
    return {str(ratio): swing_high - diff * ratio for ratio in _RETRACEMENT_RATIOS}


def fib_extensions(swing_high: Decimal, swing_low: Decimal) -> dict[str, Decimal]:
    """Extension targets (1.272/1.618) projected from `swing_low` through the move."""
    diff = swing_high - swing_low
    return {str(ratio): swing_low + diff * ratio for ratio in _EXTENSION_RATIOS}


# ---------------------------------------------------------------------------
# Deceleration
# ---------------------------------------------------------------------------


def deceleration(candles: list[Candle], n: int = 3) -> bool:
    """True if the last `n` candles have strictly shrinking bodies (§1.4)."""
    if len(candles) < n:
        return False
    recent = candles[-n:]
    bodies = [abs(float(c.close - c.open)) for c in recent]
    return all(bodies[i] > bodies[i + 1] for i in range(len(bodies) - 1))
