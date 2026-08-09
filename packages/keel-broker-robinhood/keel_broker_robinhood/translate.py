"""The one place keel's order model becomes Robinhood's order-body and status vocabulary.

Everything Robinhood-specific about order shape and state spelling lives here, mirroring
`keel_broker_coinbase.translate`. Two things set this venue apart from Coinbase and make this
module worth reading closely rather than skimming as a copy:

1. Robinhood's `market_order_config` accepts only `asset_quantity` -- there is no quote-sized
   market order on this API at all. `MarketIOCByQuote` is therefore refused here, not merely
   left unhandled, so a future engine change that starts routing an unsupported kind through this
   adapter fails loudly at translation time instead of silently sending a malformed body.
2. Robinhood spells a cancelled order's terminal state `canceled` (American, single `l`); keel's
   port spells it `CANCELLED`. `STATE_TO_PORT_STATUS` is the one place those two spellings meet,
   so nobody downstream has to remember which venue uses which.

Decimals render via `str()` everywhere in this module so an order's size, price, or stop can
never be perturbed by a float round-trip on the live-money path.
"""

from __future__ import annotations

from typing import Any, assert_never

from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_broker_api.port import UnsupportedOrder
from keel_core.types import Side

#: Robinhood's crypto trading API only ever settles against USD -- there is no USDC, USDT, or any
#: other quote leg. This is the only quote currency `to_symbol` will accept; anything else names
#: a different settlement asset and must be refused rather than silently rewritten.
QUOTE_CURRENCY: str = "USD"

#: Robinhood's docs show `time_in_force` as `"gtc"` on every resting order example; IOC is
#: undocumented and unavailable on this API (market orders are IOC-by-construction and carry no
#: `time_in_force` field at all -- see `to_order_body`). Hardcoding this constant, rather than
#: threading a parameter through, keeps a caller from ever asking this venue for a time-in-force
#: it cannot honor.
TIME_IN_FORCE: str = "gtc"

#: Robinhood's order `state` -> keel's port `status`. This table is the only place the American
#: `canceled` (Robinhood) and the doubled-`l` `CANCELLED` (the port) meet; every other value maps
#: by capitalizing the venue's spelling. Keeping the mapping explicit, rather than deriving one
#: from the other programmatically, means a new Robinhood state added by a future API version
#: fails to translate (via `to_port_status`'s `PENDING` fallback) instead of silently guessing.
STATE_TO_PORT_STATUS: dict[str, str] = {
    "open": "OPEN",
    "canceled": "CANCELLED",
    "filled": "FILLED",
    "failed": "FAILED",
    "pending": "PENDING",
}


def to_symbol(product_id: str) -> str:
    """Render a keel product id as Robinhood's `symbol`, refusing anything not settled in USD.

    Robinhood's crypto API accepts only USD-quoted symbols. Rewriting `BTC-USDC` to `BTC-USD`
    would silently substitute a different settlement asset on the live-money path -- the caller
    asked to trade against USDC and would be filled against USD instead, with no error raised
    anywhere. Passing `BTC-USDC` through unchanged would fare no better: Robinhood would reject
    it, and that rejection looks exactly like an outage to anything watching order placement.
    Refusing by name, here, is the only option that is honest about what happened. The same
    reasoning applies to a product id that is not `BASE-QUOTE` shaped at all: guessing a split
    would risk exactly the same silent substitution.
    """
    parts = product_id.split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UnsupportedOrder(
            f"robinhood requires a BASE-QUOTE product id, got {product_id!r}"
        )
    base, quote = parts
    if quote.upper() != QUOTE_CURRENCY:
        raise UnsupportedOrder(
            f"robinhood only trades USD-quoted symbols; product {product_id!r} quotes "
            f"{quote.upper()!r}, which would settle against a different asset than requested"
        )
    return f"{base.upper()}-{quote.upper()}"


def to_side(side: Side) -> str:
    """Render keel's `Side` as Robinhood's lowercase order `side` (`"buy"` / `"sell"`)."""
    return "buy" if side is Side.BUY else "sell"


def to_price_side(side: Side) -> str:
    """Which quote leg (`"bid"` / `"ask"`) prices a preview for `side`.

    A BUY is filled at the ask (what a seller will take); a SELL is filled at the bid (what a
    buyer will pay). Pricing a BUY preview off the bid, or a SELL preview off the ask, would show
    the wrong side of the spread -- optimistic in exactly the direction that makes a synthesized
    preview look better than the fill it is meant to estimate.
    """
    return "ask" if side is Side.BUY else "bid"


def to_order_body(spec: OrderSpec, *, client_order_id: str) -> dict[str, Any]:
    """Render `spec` as the JSON body for `POST /api/v2/crypto/trading/orders/`.

    Decimals render via `str()`, never float, so no order size, limit price, or stop price can be
    perturbed by a float round-trip between here and the wire.
    """
    match spec:
        case MarketIOCByQuote():
            # Robinhood's `market_order_config` takes only `asset_quantity` -- there is no
            # quote-sized market order on this API. Synthesising one by dividing an estimated
            # price by the requested quote spend would substitute a different sizing basis (an
            # estimate, taken moments before placement) for the one the caller actually asked
            # for, on the live-money path, and it would do so silently. The adapter's capability
            # declaration already excludes this kind; this is the second gate, so a future bug
            # that routes a `MarketIOCByQuote` past the first gate still cannot place an order.
            raise UnsupportedOrder(
                "robinhood's market_order_config accepts only asset_quantity; there is no "
                "quote-sized market order on this API, and synthesizing one by dividing an "
                "estimated price would substitute a different sizing basis on the live-money path"
            )
        case MarketIOCByBase():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "side": to_side(spec.side),
                "type": "market",
                "market_order_config": {"asset_quantity": str(spec.base_size)},
            }
        case LimitGTC():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "side": to_side(spec.side),
                "type": "limit",
                "limit_order_config": {
                    "asset_quantity": str(spec.base_size),
                    "limit_price": str(spec.limit_price),
                    "time_in_force": TIME_IN_FORCE,
                },
            }
        case StopLimitGTC():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "side": to_side(spec.side),
                "type": "stop_limit",
                "stop_limit_order_config": {
                    "asset_quantity": str(spec.base_size),
                    "limit_price": str(spec.limit_price),
                    "stop_price": str(spec.stop_price),
                    "time_in_force": TIME_IN_FORCE,
                },
            }
        case _:
            assert_never(spec)


def to_port_status(state: str | None) -> str:
    """Robinhood order `state` -> the port's status vocabulary, defaulting to `"PENDING"`.

    An unrecognised or missing state means the adapter does not actually know the order's
    outcome -- not that the order failed. `PENDING` keeps it under observation so reconciliation
    keeps polling; `FAILED` would declare a terminal outcome nobody observed, and the engine could
    then re-enter a position whose original order is, for all this adapter knows, still live at
    the venue. Silence is not evidence of failure, and this function must not treat it as such.
    """
    if state is None:
        return "PENDING"
    return STATE_TO_PORT_STATUS.get(state, "PENDING")


__all__ = [
    "QUOTE_CURRENCY",
    "STATE_TO_PORT_STATUS",
    "TIME_IN_FORCE",
    "to_order_body",
    "to_port_status",
    "to_price_side",
    "to_side",
    "to_symbol",
]
