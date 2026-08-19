"""The one place keel's order model becomes Alpaca's order-body and status vocabulary.

Everything Alpaca-specific about order shape and state spelling lives here, mirroring
`keel_broker_coinbase.translate` and `keel_broker_robinhood.translate`. Three Alpaca
specifics set it apart from both siblings:

1. Equity symbols carry no quote leg: keel's `AAPL-USD` is Alpaca's `AAPL`. The USD quote
   leg is still REQUIRED on input and anything else is refused, because accepting `AAPL-EUR`
   would trade a different settlement asset than the caller named.
2. Market orders are sized by `qty` OR `notional` -- the only venue in this workspace whose
   market surface covers BOTH of the port's sizing bases natively, which is why this is the
   first adapter that can declare `market_ioc_quote`.
3. Alpaca spells a cancelled order's terminal state `canceled` (single `l`); keel's port
   spells it `CANCELLED`. `STATUS_TO_PORT_STATUS` is the only place the two meet.

Money and size values render through `_render` (fixed-point), never `str()` and never
`float`: `str(Decimal)` switches to scientific notation at small magnitudes, and `"1E-8"`
in a `notional`/`qty` field is a malformed order body -- the failure
`keel_broker_robinhood.translate._render` documents, inherited verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, assert_never

from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_broker_api.port import UnsupportedOrder
from keel_core.types import Granularity, Side

#: Alpaca's equities settle in USD only. A product id quoting anything else names a
#: different settlement asset and is refused rather than rewritten.
QUOTE_CURRENCY: str = "USD"

#: keel `Granularity` -> Alpaca v2 bars `timeframe`. Exactly the three series the PRD
#: commits to (FR-5): 15-minute confirmation candles, hourly trading bars, daily bias
#: bars. Every other granularity the port defines has NO Alpaca mapping here, so
#: `to_timeframe` refuses it rather than approximating it -- a silently-substituted
#: timeframe corrupts every downstream indicator.
TIMEFRAME_BY_GRANULARITY: dict[Granularity, str] = {
    Granularity.FIFTEEN_MINUTE: "15Min",
    Granularity.ONE_HOUR: "1Hour",
    Granularity.ONE_DAY: "1Day",
}

#: Alpaca order `status` -> keel's port status. The venue's enum is taken verbatim from
#: the order schema (docs.alpaca.markets, "Order": new, partially_filled, filled,
#: done_for_day, canceled, expired, replaced, pending_cancel, pending_replace, accepted,
#: pending_new, accepted_for_bidding, stopped, rejected, suspended, calculated, held).
#:
#: Judgement calls, so they are written down: `done_for_day` maps to PENDING, not OPEN --
#: the order exists at the venue but is not working until the next session, and PENDING is
#: the spelling that keeps reconciliation observing rather than acting. `replaced` maps to
#: CANCELLED because this id is terminal (its replacement carries a different id); an
#: unmapped status resolves to PENDING, never FAILED -- the
#: `keel_broker_robinhood.translate.to_port_status` rule: silence is not evidence of death.
STATE_TO_PORT_STATUS: dict[str, str] = {
    "new": "OPEN",
    "accepted": "OPEN",
    "accepted_for_bidding": "OPEN",
    "partially_filled": "OPEN",
    "pending_new": "PENDING",
    "pending_cancel": "PENDING",
    "pending_replace": "PENDING",
    "done_for_day": "PENDING",
    "calculated": "PENDING",
    "held": "PENDING",
    "filled": "FILLED",
    "canceled": "CANCELLED",
    "expired": "EXPIRED",
    "replaced": "CANCELLED",
    "rejected": "FAILED",
    "stopped": "FAILED",
    "suspended": "FAILED",
}


def _render(value: Decimal) -> str:
    """Render a money or size `Decimal` positionally, for a JSON field that has no
    exponent form.

    `format(value, "f")` is the fixed-point renderer: positional at every magnitude, no
    exponent ever, and unlike `f"{value:.8f}"` it neither truncates nor rounds, so the
    string still carries the caller's exact value. Fractional shares make this load-
    bearing here the same way satoshi quantities do at Robinhood.
    """
    return format(value, "f")


def to_symbol(product_id: str) -> str:
    """Render a keel product id as Alpaca's symbol, refusing anything not settled in USD.

    Alpaca's equity symbols carry no quote leg, so `AAPL-USD` becomes `AAPL` -- but the
    quote leg is validated first, because a non-USD product id would settle against a
    different asset than the caller named, silently. A product id that is not
    `BASE-QUOTE` shaped at all is refused rather than guessed at, for the same reason as
    `keel_broker_robinhood.translate.to_symbol` refuses it.
    """
    parts = product_id.split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UnsupportedOrder(
            f"alpaca requires a BASE-QUOTE product id, got {product_id!r}"
        )
    base, quote = parts
    if quote.upper() != QUOTE_CURRENCY:
        raise UnsupportedOrder(
            f"alpaca only trades USD-quoted equities; product {product_id!r} quotes "
            f"{quote.upper()!r}, which would settle against a different asset than requested"
        )
    return base.upper()


def to_timeframe(granularity: Granularity) -> str:
    """Map a `Granularity` onto an Alpaca bars `timeframe`, refusing unmapped ones.

    `ValueError` is the port's sanctioned "this venue does not serve that timeframe"
    signal -- the conformance suite's `_any_candles` helper catches it per granularity,
    and a caller who reads it goes looking for a supported series instead of receiving a
    silently-wrong one.
    """
    try:
        return TIMEFRAME_BY_GRANULARITY[granularity]
    except KeyError:
        supported = ", ".join(sorted(set(TIMEFRAME_BY_GRANULARITY.values())))
        raise ValueError(
            f"alpaca does not serve timeframe {granularity.value!r} "
            f"(supported timeframes: {supported})"
        ) from None


def to_side(side: Side) -> str:
    """Render keel's `Side` as Alpaca's lowercase order `side` (`"buy"` / `"sell"`)."""
    return "buy" if side is Side.BUY else "sell"


def to_order_body(spec: OrderSpec, *, client_order_id: str) -> dict[str, Any]:
    """Render `spec` as the JSON body for `POST /v2/orders`.

    Two venue rules shape the market legs (docs.alpaca.markets, "Create Order"):

    * `notional` (dollar amount) works ONLY with `type: market` and `time_in_force:
      day`, and cannot be combined with `qty` -- so `MarketIOCByQuote` pins all three.
    * `qty` is fractionable, and fractional quantities pass through `_render` unchanged:
      rounding here would change the position size the caller asked for.

    `extended_hours: False` is sent on EVERY body. Overnight/extended sessions are OFF by
    posture (PRD FR-9: thinner liquidity would hold the #350 spread gate constantly), and
    stating it explicitly keeps a future default change at the venue from turning it on.
    """
    match spec:
        case MarketIOCByQuote():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "notional": _render(spec.quote_size),
                "side": to_side(spec.side),
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
            }
        case MarketIOCByBase():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "qty": _render(spec.base_size),
                "side": to_side(spec.side),
                "type": "market",
                "time_in_force": "day",
                "extended_hours": False,
            }
        case LimitGTC():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "qty": _render(spec.base_size),
                "side": to_side(spec.side),
                "type": "limit",
                "time_in_force": "gtc",
                "limit_price": _render(spec.limit_price),
                "extended_hours": False,
            }
        case StopLimitGTC():
            return {
                "symbol": to_symbol(spec.product_id),
                "client_order_id": client_order_id,
                "qty": _render(spec.base_size),
                "side": to_side(spec.side),
                "type": "stop_limit",
                "time_in_force": "gtc",
                "stop_price": _render(spec.stop_price),
                "limit_price": _render(spec.limit_price),
                "extended_hours": False,
            }
        case _:
            assert_never(spec)


def to_port_status(status: str | None) -> str:
    """Alpaca order `status` -> the port's vocabulary, defaulting to `"PENDING"`."""
    if status is None:
        return "PENDING"
    return STATE_TO_PORT_STATUS.get(status, "PENDING")


def to_rfc3339(ts: int) -> str:
    """Render epoch seconds as the RFC3339 UTC form Alpaca's `start`/`end` take.

    Seconds precision with an explicit `Z`: no offset to misread as local time, no
    fractional part for the venue to parse differently than we wrote it.
    """
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_unix_seconds(value: str) -> int:
    """Parse an Alpaca RFC3339 timestamp into epoch seconds.

    Venue timestamps can carry fractional seconds and (per the schema) explicit offsets;
    fractional seconds truncate because `Candle.ts` is whole seconds and a bar's open
    time is second-aligned anyway.

    An offset-less timestamp is REFUSED, never read as local time: without an offset
    `fromisoformat` yields a naive datetime whose `.timestamp()` silently assumes the
    host's zone, so the same bar would timestamp differently per machine. `ValueError`
    is this module's refusal signal (`to_timeframe`'s), and refusing is the fail-closed
    direction -- the venue's contract sends `Z` or an explicit offset, so anything else
    is garbage, not a zone to guess.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            f"alpaca timestamp {value!r} carries no UTC offset; refusing to read a naive "
            "datetime as local time"
        )
    return int(parsed.timestamp())


__all__ = [
    "QUOTE_CURRENCY",
    "STATE_TO_PORT_STATUS",
    "TIMEFRAME_BY_GRANULARITY",
    "to_order_body",
    "to_port_status",
    "to_rfc3339",
    "to_side",
    "to_symbol",
    "to_timeframe",
    "to_unix_seconds",
]
