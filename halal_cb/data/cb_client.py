"""Thin, injectable wrapper around the Coinbase Advanced Trade REST API.

`cb_client` is the **only** module in `halal_cb` that talks to the network. It never
instantiates its own transport -- a `transport` (duck-typed like `coinbase.rest.RESTClient`,
or any fake with matching method signatures) is injected by the caller, so tests exercise it
against canned JSON fixtures with zero live network calls.

In production, inject the real client:

    from coinbase.rest import RESTClient
    from halal_cb.config import load_secrets

    secrets = load_secrets()
    transport = RESTClient(api_key=secrets["api_key"], api_secret=secrets["api_secret"])
    client = CoinbaseClient(transport)

Order *placement* is intentionally out of scope for Phase 1 -- `place_order` raises
`NotImplementedError` until halal review + risk rails land in Phase 3.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from halal_cb.types import Candle, Granularity, Side


class Transport(Protocol):
    """Structural interface `CoinbaseClient` depends on -- matches `coinbase.rest.RESTClient`."""

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, **kwargs: Any
    ) -> Any: ...

    def get_product(self, product_id: str, **kwargs: Any) -> Any: ...

    def get_accounts(self, **kwargs: Any) -> Any: ...

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict, **kwargs: Any
    ) -> Any: ...


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a plain dict OR a `coinbase-advanced-py` `BaseResponse` object.

    Fixtures in tests are plain dicts (real-shaped JSON); the real transport returns
    `BaseResponse` subclasses that expose attributes instead of dict keys. Both support
    dict-style `[]` for present keys, but only dicts have `.get`, so branch on type.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _candle_from_raw(raw: Any) -> Candle:
    return Candle(
        ts=int(_field(raw, "start")),
        open=Decimal(_field(raw, "open")),
        high=Decimal(_field(raw, "high")),
        low=Decimal(_field(raw, "low")),
        close=Decimal(_field(raw, "close")),
        volume=Decimal(_field(raw, "volume")),
    )


class CoinbaseClient:
    """Wraps an injected Coinbase REST transport with typed, `Decimal`-safe methods."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def get_candles(
        self, product_id: str, granularity: Granularity, start: int, end: int
    ) -> list[Candle]:
        """Fetch candles for `product_id` between `start`/`end` (epoch seconds), ascending."""
        response = self._transport.get_candles(
            product_id=product_id,
            start=str(start),
            end=str(end),
            granularity=granularity.value,
        )
        raw_candles = _field(response, "candles", []) or []
        candles = [_candle_from_raw(raw) for raw in raw_candles]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_spot(self, product_id: str) -> Decimal:
        """Return the current spot price for `product_id`."""
        response = self._transport.get_product(product_id=product_id)
        price = _field(response, "price")
        if price is None:
            raise ValueError(f"get_spot({product_id!r}): response has no 'price' field")
        return Decimal(price)

    def get_accounts(self) -> list[dict]:
        """Return authenticated account balances, keyed by currency."""
        response = self._transport.get_accounts()
        raw_accounts = _field(response, "accounts", []) or []
        accounts = []
        for raw in raw_accounts:
            balance = _field(raw, "available_balance") or {}
            accounts.append(
                {
                    "uuid": _field(raw, "uuid"),
                    "currency": _field(raw, "currency"),
                    "available_balance": Decimal(_field(balance, "value", "0")),
                    "default": bool(_field(raw, "default", False)),
                    "active": bool(_field(raw, "active", False)),
                }
            )
        return accounts

    def preview_order(self, product_id: str, side: Side, order_configuration: dict) -> dict:
        """Preview an order (no funds moved) -- returns `Decimal` money fields + any errors."""
        response = self._transport.preview_order(
            product_id=product_id,
            side=side.value if isinstance(side, Side) else side,
            order_configuration=order_configuration,
        )
        decimal_fields = (
            "order_total",
            "commission_total",
            "quote_size",
            "base_size",
            "best_bid",
            "best_ask",
        )
        result: dict[str, Any] = {}
        for key in decimal_fields:
            value = _field(response, key)
            if value is not None:
                result[key] = Decimal(value)
        result["errs"] = _field(response, "errs", []) or []
        result["warning"] = _field(response, "warning", []) or []
        return result

    def place_order(
        self, product_id: str, side: Side, order_configuration: dict
    ) -> dict:
        """Order placement is out of Phase-1 scope -- added in Phase 3 with halal + risk rails."""
        raise NotImplementedError("Order placement is added in Phase 3")
