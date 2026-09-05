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
class EquityReading:
    """One cycle's mark-to-market equity reading, as the agent computed it (#698).

    `mode` is `"paper"` or `"live"` -- the same partition `agent._clear_live_mode_if_needed`
    enforces on the shared high-water mark. Two modes share one database (ADR 0002 gives each
    profile its own), and their equities are unrelated accounts: a reader that blends them draws
    a cliff at the flip and calls it a drawdown.

    `cash` and `unrealized` are `None` for "not recorded", never zero -- the `orders.filled_
    quantity` convention. A cycle can know its total equity while the split is unavailable (an
    unseeded paper account, a broker that answered for the total but not per-currency), and
    writing a zero there would state a flat position that was never observed.

    `hwm` is the high-water mark AFTER this reading was folded in, so a row carries the rail-11
    ceiling that was actually in force when the agent acted on it -- the chart's drawdown
    overlay reads it rather than recomputing a monotonic maximum the engine may have rebased
    (`execution.equity.record_external_flow` shifts the HWM on a declared deposit).
    """

    ts: int
    mode: str
    equity: Decimal
    cash: Decimal | None
    unrealized: Decimal | None
    hwm: Decimal


@dataclass(frozen=True)
class CycleBalance:
    """One cycle's OBSERVED available/total balance for ONE currency, straight off the venue
    (#719).

    Per CURRENCY and deliberately not folded into `EquityReading.cash`: that field is already a
    cross-currency SUM (`agent._mark_to_market_parts`'s stated no-FX bound), and adding a second
    currency's balance into it 1:1 is exactly the mistake that bound exists to name. This type
    keeps every currency's own reading separate so a caller never has to un-sum one to get here.

    `mode` is the same `"paper"`/`"live"` partition `EquityReading.mode` carries -- written from
    the SAME `equity_state_mode` read as that cycle's equity point (see
    `execution.equity._append_equity_point`), so one cycle cannot answer "which mode" two
    different ways.

    `available` and `total` are `None` for NOT OBSERVED, never zero -- `EquityReading.cash`'s own
    convention, extended per field: the venue can answer one leg of a currency's balance and not
    the other, and writing zero for the unread side would assert a balance nobody saw.
    """

    ts: int
    mode: str
    currency: str
    available: Decimal | None
    total: Decimal | None


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


__all__ = ["Granularity", "Side", "Candle", "EquityReading", "CycleBalance", "Profile"]
