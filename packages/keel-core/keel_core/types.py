"""Shared value types used across keel modules.

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


@dataclass(frozen=True)
class Profile:
    """The user's own settings, as opposed to operational state or file configuration.

    `autonomous` is the single choice today: when true, the agent places rule-generated orders
    without asking. It is stored in the database (not `config.yaml`) and re-read once per
    cycle, so turning it off takes effect on the NEXT cycle rather than the next restart.
    """

    autonomous: bool = False
    #: `None` = the choice never lapses. Otherwise autonomy stops applying at this timestamp,
    #: which is how the time bound of the removed bypass-arm token is preserved for anyone who
    #: wants it -- a forgotten `autonomy on` need not grant unattended trading forever.
    autonomous_until: int | None = None
    updated_ts: int = 0

    def is_autonomous(self, now_ts: int) -> bool:
        """Whether autonomy actually applies at `now_ts`, honouring any expiry.

        Strict `now_ts < autonomous_until`, matching the freshness convention used throughout
        this codebase: the instant the expiry is reached, autonomy is over.
        """
        if not self.autonomous:
            return False
        if self.autonomous_until is None:
            return True
        return now_ts < self.autonomous_until


__all__ = ["Granularity", "Side", "Candle", "Profile"]
