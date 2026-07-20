"""Thin, injectable wrapper around the Coinbase Advanced Trade REST API.

`cb_client` is the **only** module in `keel` that talks to the network. It never
instantiates its own transport -- a `transport` (duck-typed like `coinbase.rest.RESTClient`,
or any fake with matching method signatures) is injected by the caller, so tests exercise it
against canned JSON fixtures with zero live network calls.

In production, inject the real client:

    from coinbase.rest import RESTClient
    from keel.config import load_secrets

    secrets = load_secrets()
    transport = RESTClient(api_key=secrets["api_key"], api_secret=secrets["api_secret"])
    client = CoinbaseClient(transport)

`place_order` talks to the live order-creation endpoint. It performs **no halal/risk checks
itself** -- callers (the Phase-3 executor + guards) must run rails and any confirm-mode gate
before calling it; `cb_client` stays a thin, dumb transport wrapper.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Protocol

from keel_core.telemetry import log_exception

from keel.types import Candle, Granularity, Side

logger = logging.getLogger(__name__)

# `order_configuration` nests exactly one config-type key (e.g. "market_market_ioc",
# "limit_limit_gtc", "stop_limit_stop_limit_gtc") whose value carries these size/price fields
# as strings -- decimal-map them the same way the other money fields are mapped.
_ORDER_CONFIG_DECIMAL_FIELDS = (
    "quote_size",
    "base_size",
    "limit_price",
    "stop_price",
    "stop_trigger_price",
)


class Transport(Protocol):
    """Structural interface `CoinbaseClient` depends on -- matches `coinbase.rest.RESTClient`."""

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, **kwargs: Any
    ) -> Any: ...

    def get_product(self, product_id: str, **kwargs: Any) -> Any: ...

    def get_products(self, **kwargs: Any) -> Any: ...

    def get_accounts(self, **kwargs: Any) -> Any: ...

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict, **kwargs: Any
    ) -> Any: ...

    def get_order(self, order_id: str, **kwargs: Any) -> Any: ...

    def cancel_orders(self, order_ids: list[str], **kwargs: Any) -> Any: ...

    def create_order(
        self,
        client_order_id: str,
        product_id: str,
        side: str,
        order_configuration: dict,
        **kwargs: Any,
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


def _decimal_map_order_configuration(raw: Any) -> dict[str, dict[str, Any]]:
    """Decimal-map size/price fields nested one level inside `order_configuration`.

    `order_configuration` is `{config_type: {field: value, ...}}` (e.g.
    `{"market_market_ioc": {"quote_size": "100.00"}}`) -- as a plain dict from fixtures, or as
    the `coinbase-advanced-py` `OrderConfiguration` wrapper (attrs) wrapping a nested wrapper
    object, matching however `_field` above distinguishes dicts from `BaseResponse` objects.
    """
    if not raw:
        return {}
    config_items = raw.items() if isinstance(raw, dict) else vars(raw).items()

    mapped: dict[str, dict[str, Any]] = {}
    for config_type, config in config_items:
        if config is None:
            continue
        config_dict = dict(config) if isinstance(config, dict) else dict(vars(config))
        for field in _ORDER_CONFIG_DECIMAL_FIELDS:
            if config_dict.get(field) is not None:
                config_dict[field] = Decimal(config_dict[field])
        mapped[config_type] = config_dict
    return mapped


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
        try:
            response = self._transport.get_candles(
                product_id=product_id,
                start=str(start),
                end=str(end),
                granularity=granularity.value,
            )
        except Exception:
            log_exception(
                logger,
                "cb_client.candles_fetch_failed",
                product=product_id,
                granularity=granularity,
                start=start,
                end=end,
            )
            raise
        raw_candles = _field(response, "candles", []) or []
        candles = [_candle_from_raw(raw) for raw in raw_candles]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_spot(self, product_id: str) -> Decimal:
        """Return the current spot price for `product_id`."""
        try:
            response = self._transport.get_product(product_id=product_id)
        except Exception:
            log_exception(logger, "cb_client.spot_fetch_failed", product=product_id)
            raise
        price = _field(response, "price")
        if price is None:
            raise ValueError(f"get_spot({product_id!r}): response has no 'price' field")
        return Decimal(price)

    def list_products(self, product_type: str = "SPOT") -> list[dict]:
        """Every tradable product on the venue, as plain dicts. READ-ONLY market metadata.

        Used only by the allowlist DISCOVERY stage (`keel assets discover`), which proposes
        candidates for human attestation -- it decides nothing. Per §5's asymmetry, a proposal
        may come from anywhere; admission goes through `compliance/screen.py`.
        """
        raw = self._transport.get_products(product_type=product_type)
        products = raw["products"] if isinstance(raw, dict) else raw.products
        out: list[dict] = []
        for product in products:
            fields = product if isinstance(product, dict) else vars(product)
            out.append(
                {
                    "product_id": fields.get("product_id"),
                    "base_name": fields.get("base_name"),
                    "quote_currency_id": fields.get("quote_currency_id"),
                    "status": fields.get("status"),
                    "trading_disabled": bool(fields.get("trading_disabled")),
                    "is_disabled": bool(fields.get("is_disabled")),
                    "view_only": bool(fields.get("view_only")),
                    "quote_24h_volume": fields.get("approximate_quote_24h_volume"),
                }
            )
        return out

    def get_accounts(self) -> list[dict]:
        """Return authenticated account balances, keyed by currency."""
        try:
            response = self._transport.get_accounts()
        except Exception:
            log_exception(logger, "cb_client.accounts_fetch_failed")
            raise
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

    def place_order(self, product_id: str, side: Side, order_configuration: dict) -> dict:
        """Place a live order -- supports market/limit/stop configs, no funds-moving retries.

        Callers (the Phase-3 executor) are responsible for running rails/guards and any
        confirm-mode gate *before* calling this -- `place_order` itself performs no risk
        checks, it only talks to the transport and normalizes the response. A fresh
        `client_order_id` is generated per call for idempotency on the Coinbase side.
        """
        side_str = side.value if isinstance(side, Side) else side
        client_order_id = str(uuid.uuid4())
        response = self._transport.create_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side_str,
            order_configuration=order_configuration,
        )

        success = bool(_field(response, "success", False))
        success_response = _field(response, "success_response") or {}
        error_response = _field(response, "error_response") or {}
        raw_order_configuration = _field(response, "order_configuration")

        result: dict[str, Any] = {
            "success": success,
            "order_id": _field(success_response, "order_id"),
            "product_id": _field(success_response, "product_id", product_id),
            "side": _field(success_response, "side", side_str),
            "client_order_id": _field(success_response, "client_order_id", client_order_id),
            "order_configuration": _decimal_map_order_configuration(raw_order_configuration),
            "error": None,
        }
        if not success:
            result["error"] = {
                "error": _field(error_response, "error"),
                "message": _field(error_response, "message"),
                "error_details": _field(error_response, "error_details"),
                "preview_failure_reason": _field(error_response, "preview_failure_reason"),
                "new_order_failure_reason": _field(error_response, "new_order_failure_reason"),
            }
        return result

    def get_order(self, order_id: str) -> dict:
        """Observed state of a previously placed order, normalized to `Decimal` money fields.

        This is what makes exit reconciliation possible at all. A placement response only says
        the order was ACCEPTED; nothing in it reveals that a resting bracket later filled, at
        what price, or for what fee. Without this the executor has to record the *expected*
        price and the *previewed* commission, so realized P&L is modelled rather than observed --
        and a stop-out closes a position the loop never notices.

        `filled_size`/`average_filled_price`/`total_fees` are absent (not zero) on an order with
        no fills yet, so they are defaulted to `Decimal("0")`: callers do arithmetic on these and
        should never have to special-case `None`. `status` is passed through verbatim -- see
        `execution.reconcile` for the values that matter.
        """
        response = self._transport.get_order(order_id=order_id)
        order = _field(response, "order") or {}
        return {
            "order_id": _field(order, "order_id", order_id),
            "product_id": _field(order, "product_id"),
            "side": _field(order, "side"),
            "status": _field(order, "status"),
            "filled_size": Decimal(_field(order, "filled_size", "0") or "0"),
            "average_filled_price": Decimal(_field(order, "average_filled_price", "0") or "0"),
            "total_fees": Decimal(_field(order, "total_fees", "0") or "0"),
        }

    def cancel_order(self, order_id: str) -> bool:
        """Cancel one resting order. `True` only if the exchange CONFIRMS the cancellation.

        Coinbase's `batch_cancel` reports success per order, so a 200 response does not mean the
        order is gone -- it can come back `{"success": false, "failure_reason": ...}` for an
        order that already filled or does not exist. Reading only the HTTP status would let
        `executor._cancel_at_exchange` record a cancel that never happened, which is precisely
        the failure it was written to prevent. No confirmation, including an empty result set,
        is treated as failure: absence of a refusal is not a confirmation.
        """
        response = self._transport.cancel_orders(order_ids=[order_id])
        results = list(_field(response, "results", []) or [])
        for result in results:
            if _field(result, "order_id") == order_id:
                return bool(_field(result, "success", False))
        # Some responses omit the echoed id; a single result for a single-id request is it.
        if len(results) == 1:
            return bool(_field(results[0], "success", False))
        return False
