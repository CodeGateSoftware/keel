"""Tests for `halal_cb.data.cb_client.CoinbaseClient`.

`CoinbaseClient` never talks to the network directly -- it wraps an injected transport that
duck-types `coinbase.rest.RESTClient`. Every test here injects a `FakeTransport` that returns
canned, real-shaped JSON loaded from `tests/fixtures/cb_*.json`. No live network calls are made.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from halal_cb.data.cb_client import CoinbaseClient
from halal_cb.types import Candle, Granularity, Side

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
    ) -> None:
        self._candles = candles
        self._product = product
        self._accounts = accounts
        self._preview = preview
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


def test_place_order_raises_not_implemented() -> None:
    client = CoinbaseClient(FakeTransport())

    with pytest.raises(NotImplementedError):
        client.place_order("BTC-USD", Side.BUY, {"market_market_ioc": {"quote_size": "100.00"}})


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
