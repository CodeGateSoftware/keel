"""The Coinbase Advanced Trade adapter: `Broker` implemented against Coinbase's REST API.

Every Coinbase-specific decision the engine must not know about lives in this package --
order-configuration shape in `translate.py`, response-probing in `transport.py`, and the
capability declaration below.

The transport is injected, never constructed here, so tests exercise the adapter against canned
fixtures with zero live network calls. It defaults to `None` so `CoinbaseAdapter()` is
constructible without credentials -- `capabilities()` is answerable offline, and any method that
actually needs the network raises a clear error rather than a confusing `AttributeError`.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.port import UnsupportedOrder
from keel_broker_api.results import Balance, FeeSummary, OrderStatus, PlaceResult, Preview
from keel_core.types import Candle, Granularity

from keel_broker_coinbase.translate import to_order_configuration
from keel_broker_coinbase.transport import Transport, _field

_CAPABILITIES = BrokerCapabilities(
    venue="coinbase",
    supported_orders=frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}
    ),
    supports_native_preview=True,
    synthesizes_preview=False,
    supports_fee_summary=True,
    quote_currencies=frozenset({"USD", "USDC"}),
    asset_classes=frozenset({"spot"}),
)


def _candle_from_raw(raw: object) -> Candle:
    return Candle(
        ts=int(_field(raw, "start")),
        open=Decimal(_field(raw, "open")),
        high=Decimal(_field(raw, "high")),
        low=Decimal(_field(raw, "low")),
        close=Decimal(_field(raw, "close")),
        volume=Decimal(_field(raw, "volume")),
    )


class CoinbaseAdapter:
    """Implements the `Broker` port against Coinbase Advanced Trade."""

    def __init__(self, transport: Transport | None = None) -> None:
        self._transport = transport

    def _require_transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError(
                "CoinbaseAdapter was constructed without a transport; "
                "inject one to make network-backed calls"
            )
        return self._transport

    def capabilities(self) -> BrokerCapabilities:
        return _CAPABILITIES

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]:
        """Fetch candles between `start_ts`/`end_ts` (epoch seconds), ascending."""
        response = self._require_transport().get_candles(
            product_id=product_id,
            start=str(start_ts),
            end=str(end_ts),
            granularity=granularity.value,
        )
        candles = [_candle_from_raw(raw) for raw in _field(response, "candles", []) or []]
        candles.sort(key=lambda c: c.ts)
        return candles

    def get_balances(self) -> list[Balance]:
        """Return per-currency balances as domain types, never Coinbase's account dicts.

        Coinbase reports `available_balance` and a separate `hold`; `total` is their sum, since
        the API exposes no single "total" field.
        """
        response = self._require_transport().get_accounts()
        balances: list[Balance] = []
        for raw in _field(response, "accounts", []) or []:
            available = Decimal(_field(_field(raw, "available_balance") or {}, "value", "0"))
            hold = Decimal(_field(_field(raw, "hold") or {}, "value", "0"))
            balances.append(
                Balance(
                    currency=_field(raw, "currency"),
                    available=available,
                    total=available + hold,
                )
            )
        return balances

    def _reject_unsupported(self, spec: OrderSpec) -> None:
        if spec.kind not in _CAPABILITIES.supported_orders:
            raise UnsupportedOrder(f"coinbase does not support order kind {spec.kind!r}")

    def preview_order(self, spec: OrderSpec) -> Preview:
        """Preview via Coinbase's own endpoint -- hence `synthetic=False`."""
        self._reject_unsupported(spec)
        response = self._require_transport().preview_order(
            product_id=spec.product_id,
            side=spec.side.value,
            order_configuration=to_order_configuration(spec),
        )
        errs = tuple(str(e) for e in (_field(response, "errs", []) or []))
        return Preview(
            product_id=spec.product_id,
            side=spec.side,
            est_base_size=Decimal(_field(response, "base_size", "0")),
            est_quote_size=Decimal(_field(response, "quote_size", "0")),
            est_fee=Decimal(_field(response, "commission_total", "0")),
            synthetic=False,
            detail={
                key: str(value)
                for key in ("best_bid", "best_ask", "order_total")
                if (value := _field(response, key)) is not None
            },
            errors=errs,
        )

    def place_order(self, spec: OrderSpec) -> PlaceResult:
        """Place a live order. A fresh `client_order_id` per call gives Coinbase idempotency."""
        self._reject_unsupported(spec)
        response = self._require_transport().create_order(
            client_order_id=str(uuid.uuid4()),
            product_id=spec.product_id,
            side=spec.side.value,
            order_configuration=to_order_configuration(spec),
        )
        success = bool(_field(response, "success", False))
        if success:
            success_response = _field(response, "success_response") or {}
            return PlaceResult(
                success=True,
                broker_order_id=_field(success_response, "order_id"),
            )
        error_response = _field(response, "error_response") or {}
        reason = _field(error_response, "message") or _field(error_response, "error")
        return PlaceResult(success=False, broker_order_id=None, reason=reason)

    def get_fee_summary(self) -> FeeSummary:
        """Map Coinbase's `transaction_summary` to a `FeeSummary`.

        `volume_window` is `"unknown"` deliberately. Coinbase's documentation does not state
        whether `advanced_trade_only_volume` is trailing-30-day or calendar-month, and the honest
        declaration is the one that stops a caller comparing it against a calendar-month cap.
        The subscription spec's §10 tracks confirming this against a live account.
        """
        response = self._require_transport().get_transaction_summary()
        fee_tier = _field(response, "fee_tier") or {}
        return FeeSummary(
            venue="coinbase",
            taker_rate=Decimal(_field(fee_tier, "taker_fee_rate", "0")),
            maker_rate=Decimal(_field(fee_tier, "maker_fee_rate", "0")),
            volume_usd=Decimal(str(_field(response, "advanced_trade_only_volume", "0"))),
            fees_usd=Decimal(str(_field(response, "advanced_trade_only_fees", "0"))),
            volume_window="unknown",
            fetched_at=int(time.time()),
        )

    def get_order(self, order_id: str) -> OrderStatus:
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
        response = self._require_transport().get_order(order_id=order_id)
        order = _field(response, "order") or {}
        return OrderStatus(
            order_id=_field(order, "order_id", order_id),
            status=_field(order, "status"),
            filled_size=Decimal(_field(order, "filled_size", "0") or "0"),
            average_filled_price=Decimal(_field(order, "average_filled_price", "0") or "0"),
            total_fees=Decimal(_field(order, "total_fees", "0") or "0"),
        )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel one resting order. `True` only if the exchange CONFIRMS the cancellation.

        Coinbase's `batch_cancel` reports success per order, so a 200 response does not mean the
        order is gone -- it can come back `{"success": false, "failure_reason": ...}` for an
        order that already filled or does not exist. Reading only the HTTP status would let
        `executor._cancel_at_exchange` record a cancel that never happened, which is precisely
        the failure it was written to prevent. No confirmation, including an empty result set,
        is treated as failure: absence of a refusal is not a confirmation.
        """
        response = self._require_transport().cancel_orders(order_ids=[order_id])
        results = list(_field(response, "results", []) or [])
        for result in results:
            if _field(result, "order_id") == order_id:
                return bool(_field(result, "success", False))
        # Some responses omit the echoed id; a single result for a single-id request is it.
        if len(results) == 1:
            return bool(_field(results[0], "success", False))
        return False


__all__ = ["CoinbaseAdapter"]
