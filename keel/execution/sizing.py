"""Position sizing (P3 Task 2).

Two sizing regimes, both `Decimal`-only (money/prices never touch `float`, and distances are
always expressed as %/price -- never pips):

- **Fixed-fractional risk sizing** (`size`): risk a fixed percentage of account equity per
  trade, sized off the stop distance in price. This is the sizing used by stop-bearing rules
  (breakout/pullback/mean-reversion) where a wrong entry has a defined invalidation price.
- **No-stop accumulation sizing** (`dca_size`): a fixed USD budget converted to quantity at the
  current price, for the DCA rule which has no stop by design (`strategy/rules/dca.py`).

`spend` is a small shared helper (qty * entry) used by callers that need to know how much
notional a sized quantity will actually spend, for order construction and per-order/per-day cap
checks (guards, P3 Task 3).
"""

from __future__ import annotations

from decimal import Decimal


def size(equity: Decimal, risk_pct: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """Fixed-fractional position size: risk `risk_pct` of `equity` over the entry-to-stop
    distance.

    `qty = (equity * risk_pct) / abs(entry - stop)`. The stop distance is taken as an absolute
    value so callers don't need to know the trade direction. Raises `ValueError` if `entry` and
    `stop` are equal (a zero stop distance would risk-size to an infinite/undefined quantity).
    """
    stop_distance = abs(entry - stop)
    if stop_distance == 0:
        raise ValueError("size: stop distance is zero (entry == stop) -- cannot size a trade")

    return (equity * risk_pct) / stop_distance


def spend(qty: Decimal, entry: Decimal) -> Decimal:
    """Notional dollars spent buying `qty` at `entry`."""
    return qty * entry


def dca_size(budget_usd: Decimal, entry: Decimal) -> Decimal:
    """No-stop accumulation sizing: convert a fixed USD `budget_usd` into quantity at `entry`.

    Used by the DCA rule, which has no stop by design -- sizing is purely budget / price.
    Raises `ValueError` if `entry` is zero or negative.
    """
    if entry <= 0:
        raise ValueError("dca_size: entry must be positive")

    return budget_usd / entry
