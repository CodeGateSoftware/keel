"""Structural transport interface, response helpers, and the network-backed Robinhood client.

Everything Robinhood-specific about *talking to the venue* lives here, so `adapter.py` and
`translate.py` never see a `requests.Response`, an HTTP status code, or a signing key. That
boundary exists for two reasons at once:

1. Testability. `Transport` is a `Protocol`, not a base class, so the adapter's tests inject a
   plain object (or a dict-backed fake) that satisfies the same shape a real `RobinhoodTransport`
   does, with zero network and zero credentials. `sign_payload`/`build_headers` are free
   functions rather than methods for the same reason: the signing rule -- the one piece of this
   module that must never silently drift from Robinhood's docs -- is unit-testable against a
   known keypair without constructing a transport, a session, or a single HTTP call.
2. Import safety. `pynacl` and `requests` are real, heavy third-party dependencies that a caller
   who only wants `capabilities()` or who is running the conformance suite against a fake
   transport should never be forced to install. Both imports are therefore deferred to call time
   (see the comments at each `import` below) so `from keel_broker_robinhood.transport import
   Transport` succeeds in an environment with neither package present.

Robinhood's v2 API paginates every list endpoint (`{"next": ..., "previous": ..., "results":
[...]}`) and answers with plain JSON dicts, so unlike the Coinbase transport there is no SDK
response-object type to accommodate here -- `_field`/`_results` exist mainly to give the adapter
one shape to depend on regardless of whether a test fixture is a bare list or a paginated dict.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Protocol


class Transport(Protocol):
    """Structural interface the adapter depends on.

    Every method returns `Any` deliberately: the Protocol's job is to pin down *which network
    calls exist and what arguments they take*, not the response shape. Response shape is
    `translate.py`'s and `adapter.py`'s problem, read through `_field`/`_results` so that a test
    fixture (a plain dict) and a live JSON response (also a plain dict, since this transport does
    its own `.json()` decoding rather than wrapping responses in SDK objects) are indistinguishable
    to callers.
    """

    def get_accounts(self) -> Any: ...

    def get_holdings(self) -> Any: ...

    def get_trading_pairs(self, symbol: str | None = None) -> Any: ...

    def get_best_bid_ask(self, symbol: str) -> Any: ...

    def get_estimated_price(self, symbol: str, side: str, quantity: str) -> Any: ...

    def create_order(self, body: dict[str, Any]) -> Any: ...

    def get_order(self, order_id: str) -> Any: ...

    def cancel_order(self, order_id: str) -> Any: ...


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read `key` from a plain dict, an attribute-bearing object, or `None`.

    `RobinhoodTransport` decodes every response with `.json()`, so in first-party use `obj` is
    always a dict -- but `Transport` is a `Protocol`, and nothing stops a caller from satisfying
    it with a client that wraps responses in objects the way `coinbase-advanced-py` does. Reading
    both shapes here costs one `isinstance` and means such a transport degrades into a confusing
    `AttributeError` at no point. `keel_broker_coinbase._field` makes the same allowance for the
    same reason.

    The `None` branch is the one that earns this a function rather than an inline `obj.get(...)`:
    `obj` is legitimately `None` for an absent nested config block (a market order has no
    `limit_order_config`) and for a 404 that `get_order`/`cancel_order` already turned into that
    sentinel, and `None.get(...)` would raise in exactly the place a caller expected a quiet
    default.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _results(response: Any) -> list[Any]:
    """Normalize a paginated response, a bare list, or `None` into a list of result rows.

    Tests inject plain dict fixtures shaped like the real endpoint (`{"results": [...]}`) or, for
    the simplest cases, a bare list standing in for "the results, already unwrapped." The live
    transport itself never hands this function anything but a page dict -- pagination is resolved
    inside `RobinhoodTransport._paginate` before the adapter ever sees a response -- but this
    function has to accept all three shapes anyway, because it is also the thing that reads each
    individual page while `_paginate` is walking `next` cursors. Silently returning `[]` for a
    shape nobody anticipated would hide a real fixture bug as "the venue has no holdings/orders/
    trading pairs today", which is indistinguishable from an empty account until something is
    quietly missing.
    """
    if response is None:
        return []
    if isinstance(response, list):
        return response
    return list(_field(response, "results", []) or [])


def sign_payload(
    private_key_b64: str, api_key: str, timestamp: int, path: str, method: str, body: str
) -> str:
    """Sign one request per Robinhood's Ed25519 scheme and return the base64 signature.

    The message is `f"{api_key}{timestamp}{path}{method}{body}"` encoded as UTF-8 -- an exact
    concatenation, not a JSON envelope or a delimited list, so every argument must already be in
    its final on-the-wire form before it reaches this function: `timestamp` as the same string
    that goes in the `x-timestamp` header, `path` as the exact request path *including the query
    string* (see `RobinhoodTransport`'s request method for why a mismatch there is a silent
    401), `method` uppercase, and `body` as the literal JSON text sent on the wire (or `""` for a
    GET with no body -- not `"{}"`, not `"null"`).

    `private_key_b64` is the base64 encoding of the raw 32-byte Ed25519 *seed* Robinhood issues
    when a credential is created -- not a PEM, not a hex string, and not the base64 *public* key
    Robinhood's own credential page asks for (that one is uploaded to Robinhood, never used here).
    `nacl.signing.SigningKey` derives the full keypair from that seed.

    `pynacl` is imported here, at call time, rather than at module load -- see the module
    docstring's "Import safety" point. This is the one function in the module that actually
    touches the crypto stack, so it is the only place that needs the import to succeed.
    """
    import nacl.signing  # deferred: see module docstring "Import safety"; keeps this module
    # importable (e.g. for `Transport`/`_field`/`_results` in tests) without pynacl installed.

    message = f"{api_key}{timestamp}{path}{method}{body}".encode()
    seed = base64.b64decode(private_key_b64)
    signing_key = nacl.signing.SigningKey(seed)
    signature = signing_key.sign(message).signature
    return base64.b64encode(signature).decode()


def build_headers(api_key: str, signature: str, timestamp: int) -> dict[str, str]:
    """Assemble the four headers Robinhood requires on every authenticated request.

    `timestamp` is taken as an `int` (epoch seconds, matching `sign_payload`'s argument) and
    rendered to `str` here, once, so the caller cannot accidentally sign one string
    representation (say, with different rounding) and send another -- the header value and the
    signed value must be byte-identical, since Robinhood recomputes the signature server-side
    over exactly what it receives.
    """
    return {
        "x-api-key": api_key,
        "x-signature": signature,
        "x-timestamp": str(timestamp),
        "Content-Type": "application/json",
    }


#: Robinhood's `next` cursor points at another page of the same endpoint. A well-behaved account
#: with a handful of holdings or a day's worth of orders resolves in one page; twenty pages is
#: already an enormous account history by any realistic measure. The cap exists because a `next`
#: cursor is server-controlled: a bug on Robinhood's side that returns a `next` link pointing at
#: itself, or at a page that never terminates, would otherwise turn one `get_holdings()` call into
#: an infinite loop that holds the account's credentials busy against a live-money venue forever.
#: Twenty pages failing to reach the end is itself a signal something is wrong, so this raises
#: rather than silently truncating.
_MAX_PAGES = 20


class RobinhoodTransport:
    """The live, network-backed `Transport`: HTTP + Ed25519 signing against `trading.robinhood.com`.

    This class, not `adapter.py`, is where every Robinhood-account concept that is really an
    *authentication* detail rather than a *trading* concept gets absorbed. `account_number` is
    the clearest example: Robinhood's orders/holdings endpoints are scoped to one account number
    per request, but which account number that is is a fact about *this credential*, not about
    the order being placed. Resolving and caching it here -- instead of requiring the caller (or
    `adapter.py`) to fetch `GET /accounts/` and thread the number through every call -- was the
    deliberate choice between two options: (a) push it up to the adapter/port layer, or (b) keep
    it entirely inside the transport. Option (a) was rejected because `account_number` is not a
    concept the `Broker` port (or any other venue) has any use for; it exists only because this
    one venue's REST API happens to require it on the query string. Letting it leak into
    `adapter.py` would plant a Robinhood-ism in the one layer that is supposed to stay
    venue-agnostic. So the adapter calls `get_holdings()` with no arguments, and never learns that
    an account number was involved at all.
    """

    def __init__(
        self,
        api_key: str,
        private_key_b64: str,
        base_url: str = "https://trading.robinhood.com",
        account_number: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._private_key_b64 = private_key_b64
        # Trailing slash stripped once here so every path builder below can assume "no trailing
        # slash on the base, leading slash on the path" and never double or drop a `/`.
        self._base_url = base_url.rstrip("/")
        self._account_number = account_number
        self._timeout = timeout

    def _account(self) -> str:
        """Return the cached account number, resolving it from `GET /accounts/` on first use.

        Caching after the first successful resolution means every subsequent call -- however
        many holdings/orders/cancel requests happen over this transport's lifetime -- costs zero
        extra requests. Resolving lazily (not in `__init__`) means constructing a transport never
        performs network I/O by itself, which matters for tests that construct one and monkeypatch
        `_request` before anything touches the network.
        """
        if self._account_number is not None:
            return self._account_number
        response = self._request("GET", "/api/v2/crypto/trading/accounts/")
        accounts = _results(response)
        if not accounts:
            raise RuntimeError(
                "robinhood account resolution failed: GET /accounts/ returned no accounts for "
                "this credential"
            )
        account_number = _field(accounts[0], "account_number")
        if not account_number:
            raise RuntimeError(
                "robinhood account resolution failed: the account row has no 'account_number' "
                "field"
            )
        # `str(...)` here (not just a type hint) matters under mypy --strict: `_field` returns
        # `Any` by design, and letting that `Any` flow straight into a `-> str` return would be
        # exactly the kind of untyped leak strict mode exists to catch.
        resolved = str(account_number)
        self._account_number = resolved
        return resolved

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Sign and send one request; decode 2xx JSON; raise for everything else but a 404.

        The signature is computed over the *exact* path sent on the wire, query string included
        -- Robinhood's server recomputes the signature from the request it actually received, so
        if the string signed here ever diverges from the string `requests` puts on the wire (a
        different query-param order, an extra trailing slash, a param added after signing), the
        result is not a helpful error: it is a 401 that looks identical to a bad key or a stale
        clock. This is the single easiest way to get this integration wrong, which is why the
        query string is built once, by hand, and reused byte-for-byte for both the signature and
        the request.

        A body is JSON-encoded once (`json.dumps`) and that exact string is both signed and sent,
        for the same reason: `requests`' own `json=` kwarg would re-serialize the dict, and
        nothing guarantees byte-for-byte agreement with whatever was signed.

        404 is special-cased into `None` here so `get_order`/`cancel_order` can treat "the venue
        does not recognise this id" as a normal, expected outcome instead of an exception --
        every other non-2xx status (401, 429, 5xx, a connection error) propagates as a raised
        exception. Swallowing those into `None` too would be the single most dangerous mistake
        available in this module: a transient 5xx or a dropped connection while polling a live
        order would then read exactly like "this order does not exist", and the adapter maps a
        `None` `get_order` result to a terminal FAILED status -- reporting a live, resting order
        as dead because a request timed out is precisely the failure this split prevents.
        """
        import requests  # deferred: see module docstring "Import safety"; only the live,
        # network-backed transport needs the HTTP stack, not the Protocol or the pure signing
        # helpers above.

        query = ""
        if params:
            # Sorted for a deterministic string: dict insertion order is an implementation detail
            # that must not silently change what gets signed from one call to the next.
            query = "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
        full_path = f"{path}{query}"

        body_str = "" if body is None else json.dumps(body)

        # Robinhood's signature is only valid for 30 seconds from this timestamp, so it is taken
        # immediately before signing and sending -- computing it earlier (e.g. once per batch of
        # requests) would risk a stale-clock 401 on whichever request goes out last.
        timestamp = int(time.time())
        signature = sign_payload(
            self._private_key_b64, self._api_key, timestamp, full_path, method.upper(), body_str
        )
        headers = build_headers(self._api_key, signature, timestamp)

        response = requests.request(
            method,
            f"{self._base_url}{full_path}",
            headers=headers,
            data=body_str if body is not None else None,
            timeout=self._timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if not response.content:
            return None
        json_response: Any = response.json()
        return json_response

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Follow `next` cursors and concatenate `results`, so callers never see a page boundary.

        Returning a `{"results": [...]}` shaped dict -- rather than the raw last page, or a bare
        list -- keeps this transport's paginated methods uniform with the endpoints that never
        paginate (`get_order`, `create_order`): every caller reaches into a response with
        `_results(response)` and gets the full, already-concatenated answer regardless of how
        many pages the venue happened to split it across. See `_MAX_PAGES` for why the follow
        loop is bounded rather than trusting `next` to terminate on its own.
        """
        results: list[Any] = []
        next_path: str | None = path
        next_params: dict[str, Any] | None = params
        pages = 0
        while next_path is not None:
            pages += 1
            if pages > _MAX_PAGES:
                raise RuntimeError(
                    f"robinhood pagination did not terminate within {_MAX_PAGES} pages "
                    f"following {path!r}; refusing to loop further"
                )
            page = self._request("GET", next_path, params=next_params)
            results.extend(_results(page))
            cursor = _field(page, "next")
            # After the first page, `next` is already a full absolute URL Robinhood hands back --
            # not a path this transport built -- so params are folded into it already and must
            # not be re-appended on the next iteration.
            if cursor:
                if cursor.startswith(self._base_url):
                    cursor = cursor[len(self._base_url) :]
                next_path = cursor
                next_params = None
            else:
                next_path = None
        return {"results": results}

    def get_accounts(self) -> Any:
        return self._paginate("/api/v2/crypto/trading/accounts/")

    def get_holdings(self) -> Any:
        return self._paginate(
            "/api/v2/crypto/trading/holdings/", params={"account_number": self._account()}
        )

    def get_trading_pairs(self, symbol: str | None = None) -> Any:
        params = {"symbol": symbol} if symbol is not None else None
        return self._paginate("/api/v2/crypto/trading/trading_pairs/", params=params)

    def get_best_bid_ask(self, symbol: str) -> Any:
        return self._paginate(
            "/api/v2/crypto/marketdata/best_bid_ask/", params={"symbol": symbol}
        )

    def get_estimated_price(self, symbol: str, side: str, quantity: str) -> Any:
        # Not paginated in practice (one symbol, one side, one quantity produces at most a
        # handful of rows), but routed through `_paginate` anyway so its shape matches every
        # other read here and the adapter never has to special-case one endpoint.
        return self._paginate(
            "/api/v2/crypto/trading/estimated_price/",
            params={"symbol": symbol, "side": side, "quantity": quantity},
        )

    def create_order(self, body: dict[str, Any]) -> Any:
        return self._request(
            "POST",
            "/api/v2/crypto/trading/orders/",
            params={"account_number": self._account()},
            body=body,
        )

    def get_order(self, order_id: str) -> Any:
        """Fetch one order; `None` only if Robinhood's 404 says this id does not exist.

        See `_request`'s docstring for why every other failure mode raises instead: this is the
        method `adapter.get_order` calls to reconcile a live position, and a `None` here becomes
        a terminal FAILED status one layer up. Reporting FAILED because of a network blip rather
        than because Robinhood actually said "no such order" would tell the engine a resting
        order is gone when it is still live on the venue.
        """
        return self._request("GET", f"/api/v2/crypto/trading/orders/{order_id}/")

    def cancel_order(self, order_id: str) -> Any:
        """Cancel one order; `None` only if Robinhood's 404 says this id does not exist.

        Same reasoning as `get_order`: a `None` here must mean "the venue has never heard of this
        id," not "something went wrong while cancelling." `adapter.cancel_order` treats anything
        else -- including a successful response whose `state` is not `"canceled"` -- as evidence
        to re-poll, precisely because a resting order that failed to cancel is still live money
        and must never be reported as gone on the strength of a raised exception it didn't get.
        """
        return self._request("POST", f"/api/v2/crypto/trading/orders/{order_id}/cancel/")


__all__ = [
    "RobinhoodTransport",
    "Transport",
    "_field",
    "_results",
    "build_headers",
    "sign_payload",
]
