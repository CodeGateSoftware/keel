"""Shared value types used across halal_cb modules.

Keep this module dependency-free (stdlib only) so every other module can import from it
without risking circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Granularity(str, Enum):
    """Coinbase candle granularities. Values match the Coinbase Advanced Trade API strings."""

    ONE_MINUTE = "ONE_MINUTE"
    FIVE_MINUTE = "FIVE_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    ONE_HOUR = "ONE_HOUR"
    SIX_HOUR = "SIX_HOUR"
    ONE_DAY = "ONE_DAY"


class Side(str, Enum):
    """Order side."""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Candle:
    """A single OHLCV candle. `ts` is the epoch-second open time of the candle."""

    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
