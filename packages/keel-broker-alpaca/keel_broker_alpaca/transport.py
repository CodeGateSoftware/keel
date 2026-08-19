"""Structural transport interface, response helpers, and the network-backed Alpaca client.

Everything Alpaca-specific about *talking to the venue* lives here, so `adapter.py` and
`translate.py` never see an HTTP status code, a header, or a credential. The boundary is
`keel_broker_robinhood.transport`'s, reused for the same two reasons: testability (the
`Transport` Protocol lets tests inject canned fixtures with zero network) and import
safety (`requests` is imported at call time, so importing the Protocol never forces the
HTTP stack on a caller that only wants `capabilities()`).

Two Alpaca specifics shape this module:

1. **Paper and live are different hosts** (`paper-api.alpaca.markets` vs
   `api.alpaca.markets`), selected by an explicit endpoint name and NOTHING else -- there
   is deliberately no free-form base-URL parameter for the trading host, so no
   configuration can ever point a paper credential at the live venue (PRD FR-11, the
   #233 capability stance). Market data is a third host (`data.alpaca.markets`) shared by
   both environments.
2. **Authentication is two headers on every request** (`APCA-API-KEY-ID` and
   `APCA-API-SECRET-KEY`), on the trading host and the data host alike.

Like Robinhood (#217 F6), this venue is NOT internally consistent about JSON quoting:
market data (bars, quotes) sends money values as UNQUOTED numbers while the account and
order objects QUOTE theirs. Every response is therefore decoded with
`json.loads(..., parse_float=Decimal)` -- the only place the original digits still exist
-- and the adapter additionally does `Decimal(str(value))` so both shapes land on the
same exact number.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote, urlencode

#: The two documented trading hosts, keyed by the endpoint name that selects one. This map
#: is the ONLY path from an environment choice to a host (docs.alpaca.markets,
#: "Authentication": paper `https://paper-api.alpaca.markets`, live
#: `https://api.alpaca.markets`).
TRADING_HOSTS: dict[str, str] = {
    "paper": "https://paper-api.alpaca.markets",
    "live": "https://api.alpaca.markets",
}

PAPER_TRADING_HOST: str = TRADING_HOSTS["paper"]
LIVE_TRADING_HOST: str = TRADING_HOSTS["live"]

#: Market data is served from a single host for both environments.
DATA_HOST: str = "https://data.alpaca.markets"

#: The market-data tiers this adapter can declare (PRD FR-5). IEX is the free tier; SIP is
#: the subscribed one. The choice is a DECLARED capability, never an assumption -- the
#: venue's server-side default is SIP, which silently fails for keys without the
#: subscription, so every market-data request names its feed explicitly.
SUPPORTED_DATA_FEEDS: frozenset[str] = frozenset({"iex", "sip"})

#: The bars endpoint's `adjustment` policy: split-adjusted candles (docs.alpaca.markets,
#: "Stock Bars" -- `raw`, `split`, `dividend`, `spin-off`, `all`). The PRD's candle policy
#: (FR-10) is "backtests on split-adjusted series; the cache records which" -- this is the
#: recorded which, stated once, in the one place that builds the request.
BAR_ADJUSTMENT: str = "split"

#: First backoff delay for a 429 without a `Retry-After` header, doubling per retry.
_BACKOFF_SECONDS: float = 0.5


class AlpacaAPIError(RuntimeError):
    """A non-2xx answer from the venue, with its status code and message kept together.

    The code is load-bearing, not decoration: Alpaca signals order rejections as HTTP
    statuses (403 insufficient buying power, 422 invalid body), so `place_order` needs the
    number to tell an explicit venue refusal from an infrastructure failure with an
    UNKNOWN outcome -- the two must not be handled the same way.
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"alpaca API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a plain dict, an attribute-bearing object, or `None`.

    The live transport always answers with dicts, but `Transport` is a Protocol and tests
    may satisfy it with attribute-bearing objects; the `None` branch covers absent nested
    blocks (e.g. an order with no `filled_avg_price`). Mirrors the sibling adapters'
    `_field` helpers.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class Transport(Protocol):
    """Structural interface the adapter depends on.

    Every method returns `Any` deliberately: the Protocol pins WHICH network calls exist
    and what arguments they take, not the response shape -- response shape is
    `translate.py`'s and `adapter.py`'s problem, read through `_field` so a test fixture
    (a plain dict) and a live JSON response are indistinguishable to callers.
    """

    def get_account(self) -> Any: ...

    def get_positions(self) -> Any: ...

    def get_clock(self) -> Any: ...

    def create_order(self, body: dict[str, Any]) -> Any: ...

    def get_order(self, order_id: str) -> Any: ...

    def cancel_order(self, order_id: str) -> Any: ...

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        feed: str,
        page_token: str | None = None,
    ) -> Any: ...

    def get_latest_quote(self, symbol: str, feed: str) -> Any: ...


class AlpacaTransport:
    """The live, network-backed `Transport`: header-authed JSON over HTTPS.

    The trading host is derived from `endpoint` and NOTHING else: the constructor accepts
    no host URL of any kind (there were `trading_host`/`data_host` keyword escapes here
    once -- zero callers, dead surface, removed), so `TRADING_HOSTS` is the only map from
    an environment choice to a host and a paper credential cannot be pointed at the live
    venue by any configuration path.
    """

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        *,
        endpoint: str = "paper",
        data_feed: str = "iex",
        timeout: float = 10.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if endpoint not in TRADING_HOSTS:
            raise ValueError(
                f"endpoint must be one of {sorted(TRADING_HOSTS)}, got {endpoint!r} -- the "
                "trading host is derived from this choice, never configured as a URL, so a "
                "paper credential cannot be pointed at the live venue"
            )
        if data_feed not in SUPPORTED_DATA_FEEDS:
            raise ValueError(
                f"data_feed must be one of {sorted(SUPPORTED_DATA_FEEDS)}, got {data_feed!r} "
                "-- the data tier is a declared capability, not a server-side default"
            )
        self._key_id = key_id
        self._secret_key = secret_key
        self._endpoint = endpoint
        self.data_feed = data_feed
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._trading_host = TRADING_HOSTS[endpoint]
        self._data_host = DATA_HOST

    @property
    def endpoint(self) -> str:
        """Which environment ("paper" | "live") this transport was constructed for."""
        return self._endpoint

    @property
    def trading_host(self) -> str:
        return self._trading_host

    @property
    def data_host(self) -> str:
        return self._data_host

    def _headers(self) -> dict[str, str]:
        """The two headers Alpaca requires on every request, both hosts alike."""
        return {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _retry_delay(self, response: Any, attempt: int) -> float:
        """How long to wait before the next attempt after a 429.

        The venue's own `Retry-After` header, when sent, IS the delay; otherwise this
        backs off exponentially from `_BACKOFF_SECONDS` (0.5s, 1s, 2s...). Alpaca's data
        endpoints advertise their limits through `X-RateLimit-*` headers and answer 429
        when crossed (PRD FR-11); keel's cycle cadence sits far below any limit, so this
        is resilience, not throughput engineering.
        """
        headers = getattr(response, "headers", None) or {}
        for name, value in headers.items():
            if str(name).lower() == "retry-after":
                try:
                    return float(str(value))
                except (TypeError, ValueError):
                    break  # a malformed hint falls through to the computed backoff
        backoff: float = _BACKOFF_SECONDS * (2 ** (attempt - 1))
        return backoff

    def _send(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request, retrying ONLY 429s, and return the raw response.

        Everything else is returned as-is (the caller decides what a 4xx means), and a
        429 that exhausts `max_attempts` is returned too -- the caller raises
        `AlpacaAPIError` from it like any other non-2xx, after the retries have run.
        """
        import requests  # deferred: see the module docstring's "import safety" note.

        response = None
        for attempt in range(1, self._max_attempts + 1):
            response = requests.request(
                method,
                url,
                headers=self._headers(),
                json=body,
                timeout=self._timeout,
            )
            if response.status_code != 429 or attempt == self._max_attempts:
                return response
            self._sleep(self._retry_delay(response, attempt))
        return response

    def _api_error(self, response: Any) -> AlpacaAPIError:
        """Build the typed error for a non-2xx response, reading the venue's message.

        Alpaca answers errors as `{"message": ...}` (docs.alpaca.markets, the shared
        error schema); an unparseable body still raises with the status, because the
        status alone already forces the right handling.
        """
        status = int(getattr(response, "status_code", 0))
        message = ""
        try:
            decoded = json.loads(response.text)
            if isinstance(decoded, dict):
                message = str(decoded.get("message", ""))
        except (ValueError, AttributeError):
            pass
        return AlpacaAPIError(status, message or f"HTTP {status} with no error body")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        host: str | None = None,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Build, send, and decode one JSON request; raise `AlpacaAPIError` for non-2xx.

        The query string is built here, by hand, from SORTED params: one deterministic
        URL per request, so a recorded call is exactly what the venue received. Like the
        Robinhood transport, `quote_via=quote` (not `quote_plus`) so a `+` in a value is
        never decoded server-side as a space, and `safe=""` so nothing rides unencoded.
        The same discipline covers PATH segments: callers percent-encode every
        interpolated id/symbol with `quote(..., safe="")`, so a `/` or `?` inside one can
        never reshape the request into a different resource.

        `parse_float=Decimal`, never `response.json()`: this venue mixes quoted and
        unquoted money fields (see the module docstring), and the parser is the only
        place an unquoted number's original digits still exist.
        """
        base = host if host is not None else self._trading_host
        query = ""
        if params:
            query = "?" + urlencode(
                [(k, v) for k, v in sorted(params.items()) if v is not None],
                quote_via=quote,
                safe="",
            )
        response = self._send(method, f"{base}{path}{query}", body=body)
        if int(getattr(response, "status_code", 0)) >= 400:
            raise self._api_error(response)
        if not response.text:
            return None
        return json.loads(response.text, parse_float=Decimal)

    def get_account(self) -> Any:
        return self._request_json("GET", "/v2/account")

    def get_positions(self) -> Any:
        # A bare array -- this endpoint has no envelope.
        return self._request_json("GET", "/v2/positions")

    def get_clock(self) -> Any:
        return self._request_json("GET", "/v2/clock")

    def create_order(self, body: dict[str, Any]) -> Any:
        return self._request_json("POST", "/v2/orders", body=body)

    def get_order(self, order_id: str) -> Any:
        """Fetch one order; `None` ONLY on a 404, like the Robinhood transport.

        `None` becomes a terminal FAILED one layer up, so any other failure must raise
        rather than launder a network blip into "this order does not exist".
        """
        try:
            return self._request_json("GET", f"/v2/orders/{quote(order_id, safe='')}")
        except AlpacaAPIError as exc:
            if exc.status_code == 404:
                return None
            raise

    def cancel_order(self, order_id: str) -> Any:
        """DELETE one order and return the HTTP status, which IS the venue's answer.

        204 No Content is Alpaca's confirmation that the cancellation happened -- unlike
        Robinhood v1's text acknowledgement, this status is a statement about the order.
        404 ("order not found") and 422 ("order status is not cancelable", e.g. already
        filled) are returned as statuses rather than raised because both are ordinary
        answers the adapter maps to `False`; every other failure raises.
        """
        path = f"/v2/orders/{quote(order_id, safe='')}"
        response = self._send("DELETE", f"{self._trading_host}{path}")
        status = int(getattr(response, "status_code", 0))
        if status < 400 or status in (404, 422):
            return status
        raise self._api_error(response)

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        feed: str,
        page_token: str | None = None,
    ) -> Any:
        """One page of bars from the data host, `next_page_token` included for the caller
        to thread (docs.alpaca.markets, "Stock Bars": `GET /v2/stocks/{symbol}/bars`)."""
        params: dict[str, Any] = {
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "feed": feed,
            "adjustment": BAR_ADJUSTMENT,
        }
        if page_token is not None:
            params["page_token"] = page_token
        return self._request_json(
            "GET",
            f"/v2/stocks/{quote(symbol, safe='')}/bars",
            host=self._data_host,
            params=params,
        )

    def get_latest_quote(self, symbol: str, feed: str) -> Any:
        """The latest NBBO quote: `{"quote": {"ap": ..., "bp": ..., ...}}`.

        The venue documents `ap`/`bp` as 0 when there is no active ask/bid -- a real
        signal the adapter treats as "no book on that side", not a price of zero.
        """
        return self._request_json(
            "GET",
            f"/v2/stocks/{quote(symbol, safe='')}/quotes/latest",
            host=self._data_host,
            params={"feed": feed},
        )


__all__ = [
    "BAR_ADJUSTMENT",
    "DATA_HOST",
    "LIVE_TRADING_HOST",
    "PAPER_TRADING_HOST",
    "SUPPORTED_DATA_FEEDS",
    "TRADING_HOSTS",
    "AlpacaAPIError",
    "AlpacaTransport",
    "Transport",
    "_field",
]
