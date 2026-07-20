"""Technical indicators: EMA fan, RSI (+ divergence), MACD, ATR, ADX/+DI/-DI, Donchian
channel, Fibonacci, deceleration.

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

import math
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
# Donchian channel
# ---------------------------------------------------------------------------


def donchian_high(candles: list[Candle], period: int) -> float:
    """Highest `high` over the last `period` candles (all candles if fewer than `period`)."""
    if not candles:
        return 0.0
    window = candles[-period:]
    return float(max(c.high for c in window))


def donchian_low(candles: list[Candle], period: int) -> float:
    """Lowest `low` over the last `period` candles (all candles if fewer than `period`)."""
    if not candles:
        return 0.0
    window = candles[-period:]
    return float(min(c.low for c in window))


# ---------------------------------------------------------------------------
# ADX / directional movement (Wilder)
# ---------------------------------------------------------------------------


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder smoothing shared by `atr`/`adx`/`directional_indicators`: the seed is the
    simple average of the first `period` samples, then each subsequent value blends in
    the new sample at weight `1/period`. Mirrors `atr()`'s back-fill: every entry before
    the seed is set to the seed value, so the result is always the same length as `values`
    with no `None`/`NaN`.
    """
    n = len(values)
    if n == 0:
        return []
    result = [0.0] * n
    seed_len = min(period, n)
    seed = sum(values[:seed_len]) / seed_len
    for i in range(seed_len):
        result[i] = seed
    for i in range(seed_len, n):
        result[i] = (result[i - 1] * (period - 1) + values[i]) / period
    return result


def directional_indicators(
    candles: list[Candle], period: int = 14
) -> tuple[list[float], list[float]]:
    """Wilder's +DI/-DI, same length as `candles`. Factored out of `adx()` since it needs
    these anyway; also usable standalone (e.g. a trend-direction filter).

    Per-bar +DM/-DM/TR are only defined from the second candle onward (they compare
    against the prior bar), so index 0 is back-filled with index 1's value -- the same
    "back-fill before there's enough data" convention `atr()` uses.
    """
    n = len(candles)
    if n == 0:
        return [], []
    if n == 1:
        return [0.0], [0.0]

    plus_dm_raw: list[float] = []
    minus_dm_raw: list[float] = []
    tr_raw: list[float] = []
    for i in range(1, n):
        high, low = float(candles[i].high), float(candles[i].low)
        prev_high, prev_low = float(candles[i - 1].high), float(candles[i - 1].low)
        prev_close = float(candles[i - 1].close)

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

        plus_dm_raw.append(plus_dm)
        minus_dm_raw.append(minus_dm)
        tr_raw.append(tr)

    smoothed_plus = _wilder_smooth(plus_dm_raw, period)
    smoothed_minus = _wilder_smooth(minus_dm_raw, period)
    smoothed_tr = _wilder_smooth(tr_raw, period)

    plus_di_tail: list[float] = []
    minus_di_tail: list[float] = []
    for pdm, mdm, tr in zip(smoothed_plus, smoothed_minus, smoothed_tr, strict=True):
        if tr == 0:
            plus_di_tail.append(0.0)
            minus_di_tail.append(0.0)
        else:
            plus_di_tail.append(100.0 * pdm / tr)
            minus_di_tail.append(100.0 * mdm / tr)

    plus_di = [plus_di_tail[0], *plus_di_tail]
    minus_di = [minus_di_tail[0], *minus_di_tail]
    return plus_di, minus_di


def adx(candles: list[Candle], period: int = 14) -> list[float]:
    """Wilder's Average Directional Index, 0-100, same length as `candles`.

    DX = 100*|+DI - -DI|/(+DI + -DI) (0.0 when +DI and -DI are both 0, avoiding a
    divide-by-zero); ADX is DX Wilder-smoothed over `period`. Built on
    `directional_indicators()`'s +DI/-DI so the shared DM/TR math isn't duplicated.
    """
    if not candles:
        return []

    plus_di, minus_di = directional_indicators(candles, period)
    dx: list[float] = []
    for plus, minus in zip(plus_di, minus_di, strict=True):
        denom = plus + minus
        dx.append(100.0 * abs(plus - minus) / denom if denom != 0 else 0.0)
    return _wilder_smooth(dx, period)


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


# -- Yang-Zhang volatility (KB §79.9) ------------------------------------------
# Trading periods per year. The source uses 261 (equity trading days); 24/7 spot crypto has
# no closed days, so 365 is the correct analogue. Stated rather than inherited, because
# swapping this constant rescales every annualised sigma this function returns.
YZ_PERIODS_PER_YEAR = 365


def yang_zhang_volatility(
    candles: list[Candle], window: int = 60, periods_per_year: int = YZ_PERIODS_PER_YEAR
) -> float | None:
    """Annualised Yang-Zhang volatility over the last `window` bars (KB §79.9).

    Yang & Zhang (2000): the first unbiased estimator independent of both the opening jump and
    the drift, and the most efficient of the range estimators. It extracts more information
    about volatility from the SAME OHLC data we already store -- a pure estimation improvement,
    not a parameter choice, so it does NOT increment the trials budget (§73.12).

        sigma^2_YZ = sigma^2_OPEN + k*sigma^2_STDEV + (1-k)*sigma^2_RS
        k = 0.34 / (1.34 + (D+1)/(D-1))

    ⚠️ **The overnight term degenerates on 24/7 spot.** `sigma^2_OPEN` measures the gap between
    one close and the next open; a continuous market has `O(t) ~= C(t-1)`, so that term tends to
    zero and the estimator reduces to `k*STDEV + (1-k)*RS`. That is not a defect -- it stays
    well defined via the Rogers-Satchell component -- but the source's "~8x more efficient than
    close-to-close" figure is derived for GAPPING markets. **Do not quote 8x for crypto without
    measuring it on our own candles**; see `docs/experiments/2026-07-20-yang-zhang-efficiency.md`
    for the measurement actually performed here.

    ⛔ **MEASURED AND NOT ADOPTED FOR CRYPTO.** The re-verification the KB asked for was run
    (`docs/experiments/2026-07-20-yang-zhang-efficiency.md`): against a high-frequency realized-
    volatility benchmark the efficiency is **1.01x on BTC, 0.81x on ETH, 0.45x on PAXG** -- no
    gain, and worse on two of three -- and Yang-Zhang is biased UPWARD by +0.03..+0.055
    annualised where close-to-close is near-unbiased. So it is deliberately **not** wired into
    ATR sizing, the 2N stop, the correlation rail or the shock detector: a systematically high
    sigma would widen stops and shrink sizes across the board. Kept because the measurement must
    stay reproducible, and because a gapping venue (equities, futures) is where it would pay.

    Returns `None` when there are too few bars (needs `window + 1`, since the overnight and
    close-to-close terms both look back one bar).
    """
    if window < 2 or len(candles) < window + 1:
        return None

    recent = candles[-(window + 1) :]
    opens, closes = [], []
    rs_terms = []
    for previous, current in zip(recent, recent[1:]):
        prev_close = math.log(float(previous.close))
        o = math.log(float(current.open))
        h = math.log(float(current.high))
        low = math.log(float(current.low))
        c = math.log(float(current.close))

        opens.append(o - prev_close)  # overnight jump
        closes.append(c - prev_close)  # close-to-close
        # Rogers-Satchell, drift-independent, measured from the bar's own open.
        rs_terms.append((h - o) * (h - c) + (low - o) * (low - c))

    count = len(closes)
    scale = periods_per_year / count

    mean_close = sum(closes) / count
    mean_open = sum(opens) / count
    var_stdev = scale * sum((r - mean_close) ** 2 for r in closes)
    var_open = scale * sum((o - mean_open) ** 2 for o in opens)
    var_rs = scale * sum(rs_terms)

    k = 0.34 / (1.34 + (count + 1) / (count - 1))
    variance = var_open + k * var_stdev + (1 - k) * var_rs
    return math.sqrt(variance) if variance > 0 else 0.0


def close_to_close_volatility(
    candles: list[Candle], window: int = 60, periods_per_year: int = YZ_PERIODS_PER_YEAR
) -> float | None:
    """Annualised close-to-close volatility -- the baseline Yang-Zhang is compared against."""
    if window < 2 or len(candles) < window + 1:
        return None
    recent = candles[-(window + 1) :]
    returns = [
        math.log(float(current.close)) - math.log(float(previous.close))
        for previous, current in zip(recent, recent[1:])
    ]
    count = len(returns)
    mean = sum(returns) / count
    variance = (periods_per_year / count) * sum((r - mean) ** 2 for r in returns)
    return math.sqrt(variance) if variance > 0 else 0.0
