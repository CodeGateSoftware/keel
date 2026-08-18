"""Zero-network tests for `keel_broker_alpaca.transport` against a faked HTTP layer.

`AlpacaTransport` is the half of this package that talks to a live-money venue, so it is
driven directly with a `_RecordingHTTP` standing in for `requests.request` -- the design
mirrored from `tests/broker_robinhood/test_transport.py`. The code under test is the code
that runs in production, minus the socket; a real network call from this file would be a
real order.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from keel_broker_alpaca.transport import (
    DATA_HOST,
    LIVE_TRADING_HOST,
    PAPER_TRADING_HOST,
    AlpacaAPIError,
    AlpacaTransport,
)

_KEY_ID = "AK-TEST-KEY-ID"
_SECRET = "test-secret"


class _FakeResponse:
    """The slice of `requests.Response` that `AlpacaTransport._send` actually touches."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}

    def json(self) -> Any:
        return json.loads(self.text)


class _RecordingHTTP:
    """Stands in for `requests.request`, recording every call and replaying responses.

    Responses may be one `_FakeResponse` (every call), a list (in order, last repeats),
    or a callable taking `(method, url, headers)`."""

    def __init__(self, responses: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: Any = None,
        json: Any = None,
        data: Any = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "json": json,
                "timeout": timeout,
            }
        )
        if callable(self._responses):
            result: _FakeResponse = self._responses(method, url, headers)
            return result
        if isinstance(self._responses, list):
            index = min(len(self.calls) - 1, len(self._responses) - 1)
            response: _FakeResponse = self._responses[index]
            return response
        single: _FakeResponse = self._responses
        return single


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a `_RecordingHTTP` over `requests.request` and hand back the installer.

    `AlpacaTransport._send` imports `requests` at call time, so patching the attribute on
    the real module is what the deferred import sees (the `tests/broker_robinhood`
    fixture's reasoning, reused verbatim).
    """
    import requests

    def install(responses: Any) -> _RecordingHTTP:
        recorder = _RecordingHTTP(responses)
        monkeypatch.setattr(requests, "request", recorder)
        return recorder

    return install


def _transport(**kwargs: Any) -> AlpacaTransport:
    kwargs.setdefault("sleep", lambda seconds: None)
    return AlpacaTransport(_KEY_ID, _SECRET, **kwargs)


def _query_of(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


# ---------------------------------------------------------------------------------------------
# Authentication on every request, on both hosts
# ---------------------------------------------------------------------------------------------


def test_every_trading_request_carries_the_key_headers(http: Any) -> None:
    """Alpaca authenticates with `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers on every
    endpoint, trading and market data alike (docs.alpaca.markets, "Authentication")."""
    recorder = http(_FakeResponse(payload={}))
    _transport().get_account()

    headers = recorder.calls[0]["headers"]
    assert headers["APCA-API-KEY-ID"] == _KEY_ID
    assert headers["APCA-API-SECRET-KEY"] == _SECRET


def test_every_market_data_request_carries_the_key_headers(http: Any) -> None:
    recorder = http(_FakeResponse(payload={"bars": []}))
    _transport().get_bars("AAPL", "1Day", "2026-08-01T00:00:00Z", "2026-08-14T00:00:00Z", "iex")

    headers = recorder.calls[0]["headers"]
    assert headers["APCA-API-KEY-ID"] == _KEY_ID
    assert headers["APCA-API-SECRET-KEY"] == _SECRET


# ---------------------------------------------------------------------------------------------
# Endpoint paths and host selection
# ---------------------------------------------------------------------------------------------


def test_trading_requests_go_to_the_paper_host_by_default(http: Any) -> None:
    recorder = http(_FakeResponse(payload={}))
    _transport().get_account()

    assert recorder.calls[0]["url"] == f"{PAPER_TRADING_HOST}/v2/account"


def test_the_live_endpoint_selects_the_live_host(http: Any) -> None:
    recorder = http(_FakeResponse(payload={}))
    _transport(endpoint="live").get_account()

    assert recorder.calls[0]["url"] == f"{LIVE_TRADING_HOST}/v2/account"


def test_market_data_requests_go_to_the_data_host(http: Any) -> None:
    recorder = http(_FakeResponse(payload={"bars": []}))
    _transport().get_bars("AAPL", "1Day", "2026-08-01T00:00:00Z", "2026-08-14T00:00:00Z", "iex")

    assert recorder.calls[0]["url"].startswith(f"{DATA_HOST}/v2/stocks/AAPL/bars?")


def test_get_bars_declares_timeframe_window_feed_and_adjustment(http: Any) -> None:
    """`feed` and `adjustment` are sent explicitly: the venue's silent defaults (sip;
    raw candles) are exactly what the capability declaration must not rely on."""
    recorder = http(_FakeResponse(payload={"bars": []}))
    _transport(data_feed="iex").get_bars(
        "AAPL", "15Min", "2026-08-01T00:00:00Z", "2026-08-14T00:00:00Z", "iex"
    )

    query = _query_of(recorder.calls[0]["url"])
    assert query == {
        "timeframe": "15Min",
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-14T00:00:00Z",
        "feed": "iex",
        "adjustment": "split",
    }


def test_get_bars_threads_the_pagination_token(http: Any) -> None:
    recorder = http(_FakeResponse(payload={"bars": []}))
    _transport().get_bars(
        "AAPL",
        "1Day",
        "2026-08-01T00:00:00Z",
        "2026-08-14T00:00:00Z",
        "iex",
        page_token="cGFnZTI=",
    )

    assert _query_of(recorder.calls[0]["url"])["page_token"] == "cGFnZTI="


def test_get_latest_quote_requests_the_documented_path_with_the_feed(http: Any) -> None:
    recorder = http(_FakeResponse(payload={"quote": {}}))
    _transport().get_latest_quote("AAPL", "iex")

    call = recorder.calls[0]
    assert call["url"] == f"{DATA_HOST}/v2/stocks/AAPL/quotes/latest?feed=iex"


def test_create_order_posts_the_body_as_json(http: Any) -> None:
    recorder = http(_FakeResponse(payload={"id": "o1", "status": "accepted"}))
    body = {"symbol": "AAPL", "notional": "100", "side": "buy", "type": "market"}
    _transport().create_order(body)

    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{PAPER_TRADING_HOST}/v2/orders"
    assert call["json"] == body


def test_get_order_and_cancel_order_use_the_order_id_path(http: Any) -> None:
    recorder = http([_FakeResponse(payload={"id": "o1"}), _FakeResponse(204)])
    transport = _transport()

    transport.get_order("o1")
    transport.cancel_order("o1")

    assert recorder.calls[0]["url"] == f"{PAPER_TRADING_HOST}/v2/orders/o1"
    assert recorder.calls[1]["url"] == f"{PAPER_TRADING_HOST}/v2/orders/o1"
    assert recorder.calls[1]["method"] == "DELETE"


def test_get_positions_and_get_clock_use_their_documented_paths(http: Any) -> None:
    recorder = http([_FakeResponse(payload=[]), _FakeResponse(payload={"is_open": True})])
    transport = _transport()

    transport.get_positions()
    transport.get_clock()

    assert recorder.calls[0]["url"] == f"{PAPER_TRADING_HOST}/v2/positions"
    assert recorder.calls[1]["url"] == f"{PAPER_TRADING_HOST}/v2/clock"


# ---------------------------------------------------------------------------------------------
# Response handling: money as Decimal, the 404 sentinel, cancel statuses
# ---------------------------------------------------------------------------------------------


def test_unquoted_json_numbers_arrive_as_decimal_never_float(http: Any) -> None:
    """Alpaca's bars and quotes send money values as UNQUOTED numbers while the account
    and order objects quote theirs -- a venue that mixes the two, exactly like Robinhood
    (#217 F6). `parse_float=Decimal` at the parser is the only place the original digits
    still exist."""
    http(_FakeResponse(text='{"bars": [{"t": "2026-08-14T14:30:00Z", "o": 132.02, "c": 131.9}]}'))
    response = _transport().get_bars(
        "AAPL", "15Min", "2026-08-01T00:00:00Z", "2026-08-14T00:00:00Z", "iex"
    )

    bar = response["bars"][0]
    assert isinstance(bar["o"], Decimal), f"got {type(bar['o'])}"
    assert bar["o"] == Decimal("132.02")
    assert bar["o"] * 3 == Decimal("396.06"), "the exactness that Decimal parsing buys"


def test_get_order_maps_a_404_to_none_and_raises_for_every_other_error(http: Any) -> None:
    """`None` means "the venue does not recognise this id" and nothing else: the adapter
    turns it into a terminal FAILED, so a 5xx laundered through `None` would report a live
    order as dead (the split `keel_broker_robinhood.transport._request` exists for)."""
    http(_FakeResponse(404, payload={"message": "order not found"}))
    assert _transport().get_order("no-such-id") is None

    for status in (401, 422, 500, 503):
        http(_FakeResponse(status, payload={"message": "boom"}))
        with pytest.raises(AlpacaAPIError) as excinfo:
            _transport().get_order("some-id")
        assert excinfo.value.status_code == status


def test_an_error_body_message_surfaces_in_the_exception(http: Any) -> None:
    http(_FakeResponse(422, payload={"message": "notional is out of range"}))
    with pytest.raises(AlpacaAPIError, match="notional is out of range"):
        _transport().get_order("some-id")


def test_cancel_returns_the_status_and_does_not_raise_for_404_or_422(http: Any) -> None:
    """The cancel answer IS the HTTP status (204 = confirmed, 404 = never existed,
    422 = no longer cancelable); the adapter maps each, and only these three are answers
    rather than errors."""
    http(_FakeResponse(204))
    assert _transport().cancel_order("o1") == 204

    http(_FakeResponse(404, payload={"message": "order not found"}))
    assert _transport().cancel_order("o1") == 404

    http(_FakeResponse(422, payload={"message": "order status is not cancelable"}))
    assert _transport().cancel_order("o1") == 422

    http(_FakeResponse(500))
    with pytest.raises(AlpacaAPIError):
        _transport().cancel_order("o1")


# ---------------------------------------------------------------------------------------------
# Rate limits: 429 backoff (FR-11)
# ---------------------------------------------------------------------------------------------


def test_a_429_retries_and_honours_the_retry_after_header(http: Any) -> None:
    recorder = http([_FakeResponse(429, headers={"Retry-After": "7"}), _FakeResponse(payload={})])
    slept: list[float] = []
    transport = _transport(sleep=slept.append)

    transport.get_account()

    assert len(recorder.calls) == 2
    assert slept == [7.0], "the venue's own Retry-After, when sent, IS the backoff"


def test_a_429_without_retry_after_backs_off_exponentially(http: Any) -> None:
    recorder = http([_FakeResponse(429), _FakeResponse(payload={})])
    slept: list[float] = []
    transport = _transport(sleep=slept.append)

    transport.get_account()

    assert len(recorder.calls) == 2
    assert slept == [0.5], "first retry backs off half a second without a venue hint"


def test_a_persistent_429_raises_after_the_attempt_budget(http: Any) -> None:
    recorder = http(_FakeResponse(429))
    slept: list[float] = []
    transport = _transport(sleep=slept.append, max_attempts=3)

    with pytest.raises(AlpacaAPIError) as excinfo:
        transport.get_account()

    assert excinfo.value.status_code == 429
    assert len(recorder.calls) == 3, "the attempt budget is the bound, not the clock"
    assert slept == [0.5, 1.0], "exponential: 0.5s then 1.0s"


def test_only_429_retries_an_outright_500_fails_at_once(http: Any) -> None:
    recorder = http(_FakeResponse(500))
    transport = _transport(sleep=lambda seconds: None)

    with pytest.raises(AlpacaAPIError):
        transport.get_account()
    assert len(recorder.calls) == 1


# ---------------------------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", ["production", "PAPER", "https://api.alpaca.markets", ""])
def test_an_unknown_endpoint_is_refused_at_construction(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        AlpacaTransport(_KEY_ID, _SECRET, endpoint=endpoint)


@pytest.mark.parametrize("feed", ["sip-plus", "IEX", ""])
def test_an_unknown_data_feed_is_refused_at_construction(feed: str) -> None:
    with pytest.raises(ValueError, match="data_feed"):
        AlpacaTransport(_KEY_ID, _SECRET, data_feed=feed)
