"""Thin, injectable wrapper around the Coinbase Advanced Trade REST API.

LEGACY SINCE #524: nothing in production constructs this client any more -- `_build_broker`
resolves coinbase through the `keel.brokers` entry points like every other venue, and the
class the registry hands back (`keel_broker_coinbase.CoinbaseAdapter`) is the one on the
live path. This module is retained because its methods answer in the port\'s shapes
(`Preview`, `PlaceResult`, `list[Balance]`, `Instrument`, `OrderStatus`, `CancelOutcome`)
and its tests pin Coinbase response-parsing behaviour against the same fixtures the adapter
suite uses; Phase B deletes it outright. It never instantiates its own transport -- a
`transport` (duck-typed like `coinbase.rest.RESTClient`, or any fake with matching method
signatures) is injected by the caller, so tests exercise it against canned JSON fixtures
with zero live network calls.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Protocol

from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import (
    Balance,
    CancelOutcome,
    Instrument,
    OrderStatus,
    PlaceResult,
    Preview,
)
from keel_broker_coinbase.translate import to_order_configuration
from keel_core.telemetry import log_exception, log_venue_failure

from keel.types import Candle, Granularity

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


def _outcome(success: Any) -> CancelOutcome:
    """Coinbase answers per order, so `success` is a statement about the ORDER --
    confirmed or refused, never a bare acknowledgement of the request."""
    return CancelOutcome.CONFIRMED if bool(success) else CancelOutcome.REFUSED


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

    def get_accounts(self) -> list[dict]:
        """Return authenticated account balances, keyed by currency.

        Logged through `log_venue_failure`, not `log_exception`: this is polled on a cadence by
        the TUI's balance refresh, so an unreachable venue would otherwise write a full
        traceback every 30s for as long as the machine is offline. Always re-raises, unchanged
        -- severity is a logging concern and rail 13 fails closed on the exception itself.
        """
        try:
            response = self._transport.get_accounts()
        except Exception:
            log_venue_failure(logger, "cb_client.accounts_fetch_failed")
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

    def get_instrument(self, product_id: str) -> Instrument | None:
        """One product's `base_increment`, in the PORT's shape (#524).

        The same bridge `get_balances` is: this client predates `keel-broker-api`, and
        `executor._base_increment_for` had to read `list_products()` and pick through raw dicts
        because that was the only catalogue read this client offered. Answering `Instrument` here
        meant the executor asked one question whether it held this client or a real adapter, so
        #524's flip of `_build_broker` to the registry needed no change on this path.

        `get_product`, not `get_products`. The caller needs ONE product; `list_products` returns
        about 900 and stays where it belongs -- `keel assets discover`, which genuinely wants the
        catalogue.

        `None` for a product this venue does not list, or whose increment is missing, unparseable
        or non-positive: all four are the same fact to a caller, and none is worth raising on.
        """
        response = self._transport.get_product(product_id=product_id)
        raw = _field(response, "product", response)
        increment = _field(raw, "base_increment")
        if increment is None:
            return None
        try:
            value = Decimal(str(increment))
        except ArithmeticError, TypeError, ValueError:
            return None
        if value <= 0:
            return None
        return Instrument(product_id=product_id, base_increment=value)

    def get_balances(self) -> list[Balance]:
        """The same read as `get_accounts`, in the PORT's shape (#524).

        This client predates `keel-broker-api` and its `get_accounts` returns venue-shaped dicts
        (`available_balance`, plus `uuid`/`default`/`active` nobody reads). The port's answer is
        `list[Balance]` -- `currency`, `available`, `total` -- and `executor._fetch_available_quote`
        had to probe for BOTH shapes, dict key or attribute, because it did not know which kind of
        broker it held.

        Teaching this client the port's shape removed the executor's fork: one question, one
        answer type, on this client and on a real adapter alike -- which is why #524's flip
        of `_build_broker` to the registry needed no further change on this path.

        `total` is `available + hold`, matching `keel_broker_coinbase.adapter.get_balances`
        exactly -- Coinbase exposes no single "total" field, and the two implementations must not
        disagree about what the word means while both exist.
        """
        try:
            response = self._transport.get_accounts()
        except Exception:
            log_venue_failure(logger, "cb_client.accounts_fetch_failed")
            raise
        balances: list[Balance] = []
        for raw in _field(response, "accounts", []) or []:
            available = Decimal(_field(_field(raw, "available_balance") or {}, "value", "0"))
            hold = Decimal(_field(_field(raw, "hold") or {}, "value", "0"))
            balances.append(
                Balance(
                    currency=str(_field(raw, "currency", "")),
                    available=available,
                    total=available + hold,
                )
            )
        return balances

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Preview an order (no funds moved), in the PORT's shape (#524).

        **One renderer, not two.** The wire configuration comes from
        `keel_broker_coinbase.translate.to_order_configuration` -- the same function the adapter
        uses -- rather than a dict this module builds itself. Until now the tree carried two
        Coinbase order renderers, and #502 stage 1 had to ship a test pinning them byte-identical
        to stop them drifting while both existed. There is one now, so there is nothing left to
        hold in agreement.

        `detail` carries `best_bid`/`best_ask` as strings because that is what the port's `Preview`
        declares and what `executor._preview_book` already reads off the port shape -- the spread
        gate (#350) and the entry-override warning (#332) both come through it unchanged.
        """
        response = self._transport.preview_order(
            product_id=spec.product_id,
            side=spec.side.value,
            order_configuration=to_order_configuration(spec),
        )
        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=Decimal(_field(response, "base_size", "0") or "0"),
            est_quote_size=Decimal(_field(response, "quote_size", "0") or "0"),
            est_fee=Decimal(_field(response, "commission_total", "0") or "0"),
            # A real quote from the venue, never an estimate this client computed.
            synthetic=False,
            detail={
                key: str(value)
                for key in ("best_bid", "best_ask", "order_total")
                if (value := _field(response, key)) is not None
            },
            errors=tuple(str(e) for e in (_field(response, "errs", []) or [])),
        )

    def place_order(self, spec: OrderSpec, *, idempotency_key: str | None = None) -> PlaceResult:
        """Place a live order, in the PORT's shape (#524).

        Callers run rails/guards and any confirm-mode gate BEFORE calling this -- `place_order`
        performs no risk checks, it only talks to the transport.

        `idempotency_key` identifies the INTENT, not the order, exactly as the port declares:
        omit it and a fresh `client_order_id` is minted per attempt, which is what this method did
        before the parameter existed and remains the default. Two identical orders a strategy
        genuinely meant to place are two orders.
        """
        client_order_id = idempotency_key or str(uuid.uuid4())
        response = self._transport.create_order(
            client_order_id=client_order_id,
            product_id=spec.product_id,
            side=spec.side.value,
            order_configuration=to_order_configuration(spec),
        )
        success = bool(_field(response, "success", False))
        success_response = _field(response, "success_response") or {}
        error_response = _field(response, "error_response") or {}
        return PlaceResult(
            success=success,
            broker_order_id=_field(success_response, "order_id"),
            reason=None
            if success
            else str(
                _field(error_response, "message")
                or _field(error_response, "error")
                or "the venue refused the order without stating a reason"
            ),
            client_order_id=client_order_id,
        )

    def get_order(self, order_id: str) -> OrderStatus:
        """Observed state of a previously placed order, in the PORT's shape (#524).

        This is what makes exit reconciliation possible at all. A placement response only says
        the order was ACCEPTED; nothing in it reveals that a resting bracket later filled, at
        what price, or for what fee. Without this the executor has to record the *expected*
        price and the *previewed* commission, so realized P&L is modelled rather than observed --
        and a stop-out closes a position the loop never notices.

        The port's `OrderStatus` is the answer since the flip's consumers were migrated; the
        dict this used to return was the last pre-port shape on the order path.

        `filled_size`/`average_filled_price`/`total_fees` are absent (not zero) on an order with
        no fills yet, so they are defaulted to `Decimal("0")`: callers do arithmetic on these and
        should never have to special-case `None`. `status` is passed through verbatim -- see
        `execution.reconcile` for the values that matter.
        """
        response = self._transport.get_order(order_id=order_id)
        order = _field(response, "order") or {}
        return OrderStatus(
            order_id=str(_field(order, "order_id", order_id)),
            status=str(_field(order, "status", "")),
            filled_size=Decimal(_field(order, "filled_size", "0") or "0"),
            average_filled_price=Decimal(_field(order, "average_filled_price", "0") or "0"),
            total_fees=Decimal(_field(order, "total_fees", "0") or "0"),
        )

    def cancel_order(self, order_id: str) -> CancelOutcome:
        """Cancel one resting order and report what the exchange said about THIS id.

        Coinbase's `batch_cancel` reports success per order, so a 200 response does not mean the
        order is gone -- it can come back `{"success": false, "failure_reason": ...}` for an
        order that already filled or does not exist. Reading only the HTTP status would let
        `executor._cancel_at_exchange` record a cancel that never happened, which is precisely
        the failure it was written to prevent. No confirmation, including an empty result set,
        is treated as a confirmation: absence of a refusal is not a confirmation. Coinbase is
        never `ACCEPTED` -- `success` is a statement about the order, not an acknowledgement of
        the request.
        """
        response = self._transport.cancel_orders(order_ids=[order_id])
        results = list(_field(response, "results", []) or [])
        for result in results:
            if _field(result, "order_id") == order_id:
                return _outcome(_field(result, "success", False))
        # Some responses omit the echoed id; a single result for a single-id request is it.
        if len(results) == 1:
            return _outcome(_field(results[0], "success", False))
        # No row for this id: the exchange did not answer about this order. `UNKNOWN`, not
        # `REFUSED` -- neither permits acting on the cancel, and only one of them is a statement
        # about the order.
        return CancelOutcome.UNKNOWN
