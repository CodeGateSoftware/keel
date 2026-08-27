"""Tests for `keel.data.cb_client.CoinbaseClient`.

`CoinbaseClient` never talks to the network directly -- it wraps an injected transport that
duck-types `coinbase.rest.RESTClient`. Every test here injects a `FakeTransport` that returns
canned, real-shaped JSON loaded from `tests/fixtures/cb_*.json`. No live network calls are made.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from keel_broker_api.results import Balance, CancelOutcome
from keel_core import telemetry

from keel.data.cb_client import CoinbaseClient
from keel.types import Candle, Granularity, Side

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open() as f:
        return json.load(f)


class FakeTransport:
    """Fake `RESTClient` -- duck-types the real transport's method signatures.

    Returns canned fixture data instead of making HTTP calls, and records the kwargs it was
    called with so tests can assert `CoinbaseClient` builds requests correctly.
    """

    def __init__(
        self,
        candles: dict | None = None,
        product: dict | None = None,
        accounts: dict | None = None,
        preview: dict | None = None,
        placed: dict | None = None,
        order: dict | None = None,
        cancel: dict | None = None,
    ) -> None:
        self._candles = candles
        self._product = product
        self._accounts = accounts
        self._preview = preview
        self._placed = placed
        self._order = order
        self._cancel = cancel
        self.calls: dict[str, dict[str, Any]] = {}

    def get_candles(
        self, product_id: str, start: str, end: str, granularity: str, **kwargs: Any
    ) -> dict:
        self.calls["get_candles"] = {
            "product_id": product_id,
            "start": start,
            "end": end,
            "granularity": granularity,
        }
        return self._candles

    def get_order(self, order_id: str, **kwargs: Any) -> dict:
        self.calls["get_order"] = {"order_id": order_id}
        return self._order

    def cancel_orders(self, order_ids: list[str], **kwargs: Any) -> dict:
        self.calls["cancel_orders"] = {"order_ids": order_ids}
        return self._cancel

    def get_product(self, product_id: str, **kwargs: Any) -> dict:
        self.calls["get_product"] = {"product_id": product_id}
        return self._product

    def get_accounts(self, **kwargs: Any) -> dict:
        self.calls["get_accounts"] = dict(kwargs)
        return self._accounts

    def preview_order(
        self, product_id: str, side: str, order_configuration: dict, **kwargs: Any
    ) -> dict:
        self.calls["preview_order"] = {
            "product_id": product_id,
            "side": side,
            "order_configuration": order_configuration,
        }
        return self._preview

    def create_order(
        self,
        client_order_id: str,
        product_id: str,
        side: str,
        order_configuration: dict,
        **kwargs: Any,
    ) -> dict:
        self.calls["create_order"] = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "side": side,
            "order_configuration": order_configuration,
        }
        return self._placed


class NoNetworkTransport:
    """A transport whose every method raises if called.

    Proves no code path hits it by accident.
    """

    def __getattr__(self, name: str) -> Any:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"unexpected network call: {name}({args!r}, {kwargs!r})")

        return _boom


# --- get_candles -------------------------------------------------------------------------


def test_get_candles_maps_json_to_typed_candles() -> None:
    transport = FakeTransport(candles=_load_fixture("cb_candles.json"))
    client = CoinbaseClient(transport)

    candles = client.get_candles(
        "BTC-USD", Granularity.ONE_DAY, start=1720915200, end=1721088000
    )

    assert len(candles) == 3
    assert all(isinstance(c, Candle) for c in candles)


def test_get_candles_maps_decimal_ohlcv_and_ts_correctly() -> None:
    transport = FakeTransport(candles=_load_fixture("cb_candles.json"))
    client = CoinbaseClient(transport)

    candles = client.get_candles(
        "BTC-USD", Granularity.ONE_DAY, start=1720915200, end=1721088000
    )
    oldest = candles[0]

    assert oldest.ts == 1720915200
    assert oldest.open == Decimal("63980.20")
    assert oldest.high == Decimal("64400.00")
    assert oldest.low == Decimal("63500.50")
    assert oldest.close == Decimal("64250.10")
    assert oldest.volume == Decimal("487.65210")
    for field in (oldest.open, oldest.high, oldest.low, oldest.close, oldest.volume):
        assert isinstance(field, Decimal)
    assert isinstance(oldest.ts, int)


def test_get_candles_sorted_ascending_by_ts() -> None:
    # The fixture returns candles newest-first, matching the real Coinbase API ordering.
    transport = FakeTransport(candles=_load_fixture("cb_candles.json"))
    client = CoinbaseClient(transport)

    candles = client.get_candles(
        "BTC-USD", Granularity.ONE_DAY, start=1720915200, end=1721088000
    )

    assert [c.ts for c in candles] == sorted(c.ts for c in candles)


def test_get_candles_passes_correct_params_to_transport() -> None:
    transport = FakeTransport(candles=_load_fixture("cb_candles.json"))
    client = CoinbaseClient(transport)

    client.get_candles("BTC-USD", Granularity.ONE_HOUR, start=1720915200, end=1721088000)

    assert transport.calls["get_candles"] == {
        "product_id": "BTC-USD",
        "start": "1720915200",
        "end": "1721088000",
        "granularity": "ONE_HOUR",
    }


def test_get_candles_empty_response_returns_empty_list() -> None:
    transport = FakeTransport(candles={"candles": []})
    client = CoinbaseClient(transport)

    candles = client.get_candles("BTC-USD", Granularity.ONE_DAY, start=0, end=1)

    assert candles == []


# --- get_spot -----------------------------------------------------------------------------


def test_get_spot_returns_decimal() -> None:
    transport = FakeTransport(product=_load_fixture("cb_product.json"))
    client = CoinbaseClient(transport)

    price = client.get_spot("BTC-USD")

    assert price == Decimal("65432.10")
    assert isinstance(price, Decimal)
    assert transport.calls["get_product"] == {"product_id": "BTC-USD"}


# --- get_balances (the port's shape, #524) ------------------------------------------------


def test_get_balances_answers_the_ports_type() -> None:
    """`Balance`, not this client's account dicts.

    The point of the method: `executor._fetch_available_quote` used to probe for a dict key OR an
    attribute because it did not know whether it held this pre-port client or a real adapter.
    Answering in the port's own type removes the fork -- one question, one shape, whichever kind
    of broker is on the other end.
    """
    client = CoinbaseClient(FakeTransport(accounts=_load_fixture("cb_accounts.json")))

    balances = client.get_balances()

    assert all(isinstance(b, Balance) for b in balances)
    btc = next(b for b in balances if b.currency == "BTC")
    assert btc.available == Decimal("0.53219871")
    usd = next(b for b in balances if b.currency == "USD")
    assert usd.available == Decimal("1042.55")


def test_get_balances_totals_available_plus_hold_like_the_adapter_does() -> None:
    """Coinbase exposes no single "total" field, so both implementations compute it -- and they
    must not disagree about what the word means while both exist.
    `keel_broker_coinbase.adapter.get_balances` sums `available_balance` and `hold`; so does this.
    """
    transport = FakeTransport(
        accounts={
            "accounts": [
                {
                    "currency": "USD",
                    "available_balance": {"value": "100.25"},
                    "hold": {"value": "9.75"},
                }
            ]
        }
    )

    balance = CoinbaseClient(transport).get_balances()[0]

    assert balance.available == Decimal("100.25")
    assert balance.total == Decimal("110.00")


def test_get_balances_reraises_an_unreachable_venue() -> None:
    """Rail 13 fails closed on the exception itself, so this must not swallow it -- the same
    contract `get_accounts` keeps."""

    class _Down:
        def get_accounts(self, **_: object) -> object:
            raise ConnectionError("venue unreachable")

    with pytest.raises(ConnectionError):
        CoinbaseClient(_Down()).get_balances()  # type: ignore[arg-type]


# --- get_accounts -------------------------------------------------------------------------


def test_get_accounts_maps_balances_to_decimal() -> None:
    transport = FakeTransport(accounts=_load_fixture("cb_accounts.json"))
    client = CoinbaseClient(transport)

    accounts = client.get_accounts()

    assert len(accounts) == 2
    btc = next(a for a in accounts if a["currency"] == "BTC")
    assert btc["available_balance"] == Decimal("0.53219871")
    assert isinstance(btc["available_balance"], Decimal)
    assert btc["default"] is True
    assert btc["active"] is True
    assert btc["uuid"] == "8bfc20d7-f7c6-4422-bf07-8243ca4169fe"

    usd = next(a for a in accounts if a["currency"] == "USD")
    assert usd["available_balance"] == Decimal("1042.55")


def test_get_accounts_returns_list_of_dicts() -> None:
    transport = FakeTransport(accounts=_load_fixture("cb_accounts.json"))
    client = CoinbaseClient(transport)

    accounts = client.get_accounts()

    assert isinstance(accounts, list)
    assert all(isinstance(a, dict) for a in accounts)


# --- preview_order ------------------------------------------------------------------------


def test_preview_order_maps_money_fields_to_decimal() -> None:
    transport = FakeTransport(preview=_load_fixture("cb_preview_order.json"))
    client = CoinbaseClient(transport)

    result = client.preview_order(
        "BTC-USD",
        Side.BUY,
        {"market_market_ioc": {"quote_size": "100.00"}},
    )

    assert result["order_total"] == Decimal("100.60")
    assert result["commission_total"] == Decimal("0.60")
    assert result["quote_size"] == Decimal("100.00")
    assert result["base_size"] == Decimal("0.00152834")
    assert isinstance(result["order_total"], Decimal)
    assert result["errs"] == []


def test_preview_order_passes_correct_params_to_transport() -> None:
    transport = FakeTransport(preview=_load_fixture("cb_preview_order.json"))
    client = CoinbaseClient(transport)
    order_configuration = {"market_market_ioc": {"quote_size": "100.00"}}

    client.preview_order("BTC-USD", Side.BUY, order_configuration)

    assert transport.calls["preview_order"] == {
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_configuration": order_configuration,
    }


# --- place_order (Phase 3) -----------------------------------------------------------------


def test_place_order_market_maps_success_response() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_market.json"))
    client = CoinbaseClient(transport)

    result = client.place_order(
        "BTC-USD",
        Side.BUY,
        {"market_market_ioc": {"quote_size": "100.00"}},
    )

    assert result["success"] is True
    assert result["order_id"] == "b1cd9a3b-4e5f-4a3c-9c8a-1f2e3d4c5b6a"
    assert result["product_id"] == "BTC-USD"
    assert result["side"] == "BUY"
    assert result["client_order_id"] == "6a5e1e4a-7c8b-4d9e-9f0a-2b3c4d5e6f7a"
    assert result["error"] is None
    assert result["order_configuration"] == {
        "market_market_ioc": {"quote_size": Decimal("100.00")}
    }
    assert isinstance(result["order_configuration"]["market_market_ioc"]["quote_size"], Decimal)


def test_place_order_market_passes_market_config_through_to_transport() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_market.json"))
    client = CoinbaseClient(transport)
    order_configuration = {"market_market_ioc": {"quote_size": "100.00"}}

    client.place_order("BTC-USD", Side.BUY, order_configuration)

    call = transport.calls["create_order"]
    assert call["product_id"] == "BTC-USD"
    assert call["side"] == "BUY"
    assert call["order_configuration"] == order_configuration
    assert call["client_order_id"]  # a client_order_id is always generated/passed


def test_place_order_limit_maps_success_response_and_decimal_fields() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_limit.json"))
    client = CoinbaseClient(transport)
    order_configuration = {
        "limit_limit_gtc": {
            "base_size": "0.00150000",
            "limit_price": "66000.00",
            "post_only": False,
        }
    }

    result = client.place_order("BTC-USD", Side.SELL, order_configuration)

    assert result["success"] is True
    assert result["order_id"] == "c2de0b4c-5f60-4b5d-ad9b-2030415263f8"
    assert result["side"] == "SELL"
    limit_config = result["order_configuration"]["limit_limit_gtc"]
    assert limit_config["base_size"] == Decimal("0.00150000")
    assert limit_config["limit_price"] == Decimal("66000.00")
    assert limit_config["post_only"] is False
    assert isinstance(limit_config["base_size"], Decimal)
    assert isinstance(limit_config["limit_price"], Decimal)

    call = transport.calls["create_order"]
    assert call["order_configuration"] == order_configuration
    assert call["side"] == "SELL"


def test_place_order_stop_limit_maps_decimal_fields() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_stop_limit.json"))
    client = CoinbaseClient(transport)
    order_configuration = {
        "stop_limit_stop_limit_gtc": {
            "base_size": "0.00150000",
            "limit_price": "60000.00",
            "stop_price": "61000.00",
            "stop_direction": "STOP_DIRECTION_STOP_DOWN",
        }
    }

    result = client.place_order("BTC-USD", Side.SELL, order_configuration)

    stop_config = result["order_configuration"]["stop_limit_stop_limit_gtc"]
    assert stop_config["base_size"] == Decimal("0.00150000")
    assert stop_config["limit_price"] == Decimal("60000.00")
    assert stop_config["stop_price"] == Decimal("61000.00")
    assert stop_config["stop_direction"] == "STOP_DIRECTION_STOP_DOWN"


def test_place_order_maps_error_response_when_not_successful() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_error.json"))
    client = CoinbaseClient(transport)

    result = client.place_order(
        "BTC-USD",
        Side.BUY,
        {"market_market_ioc": {"quote_size": "100.00"}},
    )

    assert result["success"] is False
    assert result["order_id"] is None
    assert result["error"] == {
        "error": "INSUFFICIENT_FUND",
        "message": "Insufficient balance in source account",
        "error_details": "",
        "preview_failure_reason": "PREVIEW_INSUFFICIENT_FUND",
        "new_order_failure_reason": "INSUFFICIENT_FUND",
    }


def test_place_order_accepts_side_as_plain_string() -> None:
    transport = FakeTransport(placed=_load_fixture("cb_place_order_market.json"))
    client = CoinbaseClient(transport)

    client.place_order("BTC-USD", "BUY", {"market_market_ioc": {"quote_size": "100.00"}})

    assert transport.calls["create_order"]["side"] == "BUY"


def test_place_order_works_with_real_response_wrapper_types() -> None:
    """`place_order` must also work when the transport returns the real typed
    `CreateOrderResponse` from `coinbase-advanced-py` (not just a plain dict).
    """
    from coinbase.rest.types.orders_types import CreateOrderResponse

    raw = _load_fixture("cb_place_order_limit.json")
    wrapped = CreateOrderResponse(dict(raw))

    class WrappedTransport:
        def create_order(self, **kwargs: Any) -> CreateOrderResponse:
            return wrapped

    client = CoinbaseClient(WrappedTransport())
    result = client.place_order(
        "BTC-USD",
        Side.SELL,
        {"limit_limit_gtc": {"base_size": "0.0015", "limit_price": "66000.00"}},
    )

    assert result["success"] is True
    assert result["order_id"] == "c2de0b4c-5f60-4b5d-ad9b-2030415263f8"
    assert result["order_configuration"]["limit_limit_gtc"]["limit_price"] == Decimal(
        "66000.00"
    )


# --- zero network in tests -----------------------------------------------------------------


def test_no_transport_method_called_beyond_what_is_needed() -> None:
    """Calling get_spot must not touch get_candles/get_accounts/preview_order on the transport."""
    transport = NoNetworkTransport()
    client = CoinbaseClient(transport)
    # Overriding get_product on the instance so only the targeted method is exercised.
    transport.get_product = lambda product_id, **kwargs: _load_fixture("cb_product.json")

    price = client.get_spot("BTC-USD")

    assert price == Decimal("65432.10")


# --- compatibility with the real coinbase-advanced-py response types ----------------------


def test_get_candles_works_with_real_response_wrapper_types() -> None:
    """`CoinbaseClient` must also work when the transport returns the real typed response
    objects from `coinbase-advanced-py` (not just plain dicts), since that's what the real
    `RESTClient` returns in production. Constructing these wrapper objects is pure/offline.
    """
    from coinbase.rest.types.product_types import GetProductCandlesResponse

    raw = _load_fixture("cb_candles.json")
    wrapped = GetProductCandlesResponse(dict(raw))

    class WrappedTransport:
        def get_candles(self, **kwargs: Any) -> GetProductCandlesResponse:
            return wrapped

    client = CoinbaseClient(WrappedTransport())
    candles = client.get_candles("BTC-USD", Granularity.ONE_DAY, start=0, end=1)

    assert len(candles) == 3
    assert candles[0].open == Decimal("63980.20")


# -- order status + cancellation ----------------------------------------------------------------


def test_get_order_normalizes_status_fill_price_and_fees():
    """The reconciliation pass needs three things a placement response cannot give: whether the
    order actually filled, at what price, and for how much in fees. `average_filled_price` and
    `total_fees` are OBSERVED, replacing the expected-price and previewed-commission estimates
    the executor records at placement time."""
    transport = FakeTransport(
        order={
            "order": {
                "order_id": "abc-123",
                "product_id": "BTC-USD",
                "side": "SELL",
                "status": "FILLED",
                "filled_size": "0.01",
                "average_filled_price": "49875.42",
                "total_fees": "2.9925",
                "completion_percentage": "100",
            }
        }
    )
    client = CoinbaseClient(transport)

    order = client.get_order("abc-123")

    assert transport.calls["get_order"] == {"order_id": "abc-123"}
    assert order["order_id"] == "abc-123"
    assert order["status"] == "FILLED"
    assert order["filled_size"] == Decimal("0.01")
    assert order["average_filled_price"] == Decimal("49875.42")
    assert order["total_fees"] == Decimal("2.9925")


def test_get_order_on_an_unfilled_order_reports_zero_fill_not_none():
    """An OPEN bracket has no fills yet. Money fields must still come back as Decimals so
    callers never have to special-case None in arithmetic."""
    transport = FakeTransport(
        order={"order": {"order_id": "abc-123", "status": "OPEN", "filled_size": "0"}}
    )
    client = CoinbaseClient(transport)

    order = client.get_order("abc-123")

    assert order["status"] == "OPEN"
    assert order["filled_size"] == Decimal("0")
    assert order["average_filled_price"] == Decimal("0")
    assert order["total_fees"] == Decimal("0")


def test_cancel_order_is_confirmed_when_the_exchange_confirms():
    transport = FakeTransport(
        cancel={"results": [{"success": True, "order_id": "abc-123"}]}
    )
    client = CoinbaseClient(transport)

    assert client.cancel_order("abc-123") is CancelOutcome.CONFIRMED
    assert transport.calls["cancel_orders"] == {"order_ids": ["abc-123"]}


def test_cancel_order_is_refused_when_the_exchange_refuses():
    """Coinbase's batch_cancel reports per-order success -- a 200 response does NOT mean the
    order was cancelled. Reading only the HTTP status would let `_cancel_at_exchange` record a
    cancel that never happened, which is the exact failure it exists to prevent."""
    transport = FakeTransport(
        cancel={
            "results": [
                {"success": False, "failure_reason": "UNKNOWN_CANCEL_ORDER", "order_id": "abc"}
            ]
        }
    )
    client = CoinbaseClient(transport)

    assert client.cancel_order("abc") is CancelOutcome.REFUSED


def test_cancel_order_is_unknown_on_an_empty_result_set():
    """No result for the id we asked about means we have no confirmation. Absence of a refusal
    is not a confirmation -- fail closed. `UNKNOWN` rather than `REFUSED` because the exchange
    did not answer about this order at all (#412); neither is settled."""
    client = CoinbaseClient(FakeTransport(cancel={"results": []}))

    assert client.cancel_order("abc") is CancelOutcome.UNKNOWN


# --- get_accounts failure severity --------------------------------------------------------
#
# `get_accounts` is polled every 30s by the TUI's balance refresh. An offline laptop must not
# write a 20-frame ERROR traceback per poll -- that is what buried a real `401 Unauthorized`
# among 60 connection failures on 2026-08-06. It must still RAISE either way: severity is a
# logging concern, and callers (rail 13 among them) depend on the exception.


class _RaisingTransport:
    """A transport whose `get_accounts` raises whatever it was handed."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def get_accounts(self, **kwargs: Any) -> dict:
        raise self._exc


def _accounts_failure_payload(caplog, exc: BaseException) -> dict:
    formatter = telemetry.JsonFormatter()
    client = CoinbaseClient(_RaisingTransport(exc))
    with caplog.at_level(logging.DEBUG, logger="keel.data.cb_client"):
        with pytest.raises(type(exc)):
            client.get_accounts()
    records = [r for r in caplog.records if r.getMessage() == "cb_client.accounts_fetch_failed"]
    assert len(records) == 1
    return json.loads(formatter.format(records[0]))


def test_get_accounts_logs_an_unreachable_venue_as_a_warning(caplog) -> None:
    exc = type("ConnectionError", (Exception,), {})("api.coinbase.com unreachable")

    payload = _accounts_failure_payload(caplog, exc)

    assert payload["level"] == "WARNING"
    assert payload["unreachable"] is True
    assert "exc" not in payload


def test_get_accounts_still_logs_a_401_as_an_error_with_its_traceback(caplog) -> None:
    exc = type("HTTPError", (Exception,), {})("401 Client Error: Unauthorized")

    payload = _accounts_failure_payload(caplog, exc)

    assert payload["level"] == "ERROR"
    assert "Traceback" in payload["exc"]


def test_get_accounts_still_raises_when_the_venue_is_unreachable(caplog) -> None:
    """Severity changed; control flow must not. Rail 13 fails closed on this exception."""
    boom = type("ConnectionError", (Exception,), {})("unreachable")
    client = CoinbaseClient(_RaisingTransport(boom))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(type(boom)):
            client.get_accounts()
