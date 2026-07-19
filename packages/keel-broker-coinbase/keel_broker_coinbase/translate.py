"""The one place keel's order model becomes Coinbase's `order_configuration` schema.

Everything Coinbase-specific about order shape lives here. Decimals render via `str()` so an
order's size is never perturbed by a float round-trip.
"""

from __future__ import annotations

from typing import assert_never

from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_core.types import Side


def to_order_configuration(spec: OrderSpec) -> dict[str, dict[str, str]]:
    """Render `spec` as a Coinbase Advanced Trade `order_configuration`."""
    match spec:
        case MarketIOCByQuote():
            return {"market_market_ioc": {"quote_size": str(spec.quote_size)}}
        case MarketIOCByBase():
            return {"market_market_ioc": {"base_size": str(spec.base_size)}}
        case LimitGTC():
            return {
                "limit_limit_gtc": {
                    "base_size": str(spec.base_size),
                    "limit_price": str(spec.limit_price),
                }
            }
        case StopLimitGTC():
            return {
                "stop_limit_stop_limit_gtc": {
                    "base_size": str(spec.base_size),
                    "stop_price": str(spec.stop_price),
                    "limit_price": str(spec.limit_price),
                    "stop_direction": _stop_direction(spec),
                }
            }
        case _:
            assert_never(spec)


def _stop_direction(spec: StopLimitGTC) -> str:
    """Coinbase requires the trigger direction explicitly.

    A protective stop on a long exits when price falls, so a SELL stop triggers on the way down.
    """
    return "STOP_DIRECTION_STOP_DOWN" if spec.side is Side.SELL else "STOP_DIRECTION_STOP_UP"
