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
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import quote, urlencode


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

    def get_orders(self, updated_at_start: str | None = None) -> Any: ...

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


#: How many times a retryable request is SENT in total, first attempt included. Four attempts with
#: the backoff below spans roughly 0.5 + 1 + 2 seconds of waiting, which comfortably outlasts the
#: sub-second bursts a 100 req/min limiter produces while staying far inside any caller's patience.
#: A cap rather than "retry until it works": a 429 that persists past several seconds is a quota
#: problem, and hiding it behind an unbounded loop would turn a visible limit into a hang.
_MAX_ATTEMPTS = 4

#: Seconds before the FIRST retry. Each subsequent wait doubles.
_BACKOFF_BASE_SEC = 0.5

#: Longest this transport will EVER wait between two attempts, including a `Retry-After` the
#: venue asked for. A server-controlled header is not a licence to block a trading loop for
#: minutes: `Retry-After: 3600` is a legal response, and honouring it literally would park the
#: caller for an hour inside what it believes is a bounded read.
_MAX_BACKOFF_SEC = 8.0

#: Status codes worth sending again. 429 is the rate limiter, and 5xx are the venue's own
#: transient failures.
#:
#: ⚠️ **404 is deliberately absent** -- `_request` turns it into `None` before this is consulted,
#: and that split is load-bearing (see `_request`). 401 is absent too: a signature or clock
#: problem is not transient, and retrying it three more times turns one clear failure into four.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Robinhood's `next` cursor points at another page of the same endpoint. A well-behaved account
#: with a handful of holdings or a day's worth of orders resolves in one page; twenty pages is
#: already an enormous account history by any realistic measure. The cap exists because a `next`
#: cursor is server-controlled: a bug on Robinhood's side that returns a `next` link pointing at
#: itself, or at a page that never terminates, would otherwise turn one `get_holdings()` call into
#: an infinite loop that holds the account's credentials busy against a live-money venue forever.
#: Twenty pages failing to reach the end is itself a signal something is wrong, so this raises
#: rather than silently truncating.
_MAX_PAGES = 20


def _retry_after_seconds(value: str | None) -> float | None:
    """The `Retry-After` header as seconds, or `None` when it does not state a usable delay.

    Only the delta-seconds form is read. RFC 9110 also permits an HTTP-date, but honouring one
    means trusting the SERVER's clock against ours, and a skewed clock would produce either an
    instant retry (useless) or a very long sleep (worse than useless) -- while the fallback here,
    exponential backoff, is already correct without any clock at all. A date-form header is
    therefore ignored rather than guessed at, which is the same posture `_field` takes toward a
    value it cannot read.

    Never raises: this is an attacker-or-bug-controlled string on a live-money path.
    """
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except (ValueError, AttributeError):
        return None
    if seconds < 0 or seconds != seconds:  # negative, or NaN
        return None
    return seconds


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """How long to wait before attempt `attempt + 1`, capped at `_MAX_BACKOFF_SEC`.

    The venue's own `Retry-After` wins when it states one -- it knows when its window resets and
    we do not -- but it is CLAMPED, never obeyed literally. See `_MAX_BACKOFF_SEC`.

    No jitter, deliberately. Jitter exists to desynchronise many clients retrying in lockstep;
    this transport is one process making one request at a time against one account, so there is
    no herd to spread out, and a nondeterministic sleep would make the retry path untestable
    without injecting a clock. If keel ever runs several Robinhood workers against one credential
    that reasoning expires.
    """
    asked = _retry_after_seconds(retry_after)
    if asked is not None:
        return min(asked, _MAX_BACKOFF_SEC)
    return min(_BACKOFF_BASE_SEC * float(2**attempt), _MAX_BACKOFF_SEC)


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._private_key_b64 = private_key_b64
        # Trailing slash stripped once here so every path builder below can assume "no trailing
        # slash on the base, leading slash on the path" and never double or drop a `/`.
        self._base_url = base_url.rstrip("/")
        self._account_number = account_number
        self._timeout = timeout
        # Injected so the retry path is testable without a test that actually sleeps for seconds.
        # A suite that waits out real backoff is a suite people stop running.
        self._sleep = sleep

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

        **GETs are retried on 429 and 5xx; nothing else is retried, ever** (#411). Robinhood
        allows 100 requests/minute sustained and 300 in a burst, and this transport had no
        backoff at all -- `get_fee_summary` alone can spend 21 requests in one call
        (`_MAX_PAGES` + the account read), so the limit is reachable in ordinary use, and a bare
        429 propagating to the caller turned a "wait half a second" into a failed cycle.

        ⚠️ **A POST is never retried, and that is not a gap.** `create_order` is the only one, and
        a 429 or a 5xx on it is an UNKNOWN outcome, not a refusal -- the venue may have accepted
        the order before the response was lost. Sending it again would place a second live order
        unless the two attempts carry the same `client_order_id`, and THIS LAYER CANNOT TELL
        WHETHER THEY DO: the body arrives already built, and an id derived from a caller's
        idempotency key looks exactly like a freshly minted uuid4 from here. Since #409 the caller
        can make a placement retry safe by passing `idempotency_key`, so the retry belongs where
        that knowledge lives -- above the adapter, not inside the transport. A transport that
        retried POSTs would be guessing, on the one request where guessing costs money.

        A retryable status on the final attempt raises exactly as it did before, so a persistent
        429 is still a visible failure rather than a hang. Each attempt re-signs: the signature is
        valid for 30 seconds, and presenting a stale one after a backoff would return a 401 that
        reads like a bad credential.
        """
        import requests  # deferred: see module docstring "Import safety"; only the live,
        # network-backed transport needs the HTTP stack, not the Protocol or the pure signing
        # helpers above.

        query = ""
        if params:
            # Sorted for a deterministic string: dict insertion order is an implementation detail
            # that must not silently change what gets signed from one call to the next.
            #
            # `quote_via=quote`, NOT the `urlencode` default of `quote_plus`: `quote_plus` encodes
            # a space as `+`, and `+` is itself a character that must survive round-tripping here.
            # A raw `+` on the wire decodes server-side as a SPACE, so a value like `1E+2` would
            # be verified against the signature as `1E+2` and then parsed as `1E 2` -- the
            # signature check passes and the venue acts on a different value than was signed.
            # (`translate._render` now keeps exponent forms out of sizes and prices, so that
            # specific pairing is closed at the source too; this is the second gate.)
            #
            # `safe=""` so nothing is left unencoded on the assumption it is harmless.
            query = "?" + urlencode(
                [(k, v) for k, v in sorted(params.items()) if v is not None],
                quote_via=quote,
                safe="",
            )
        full_path = f"{path}{query}"

        body_str = "" if body is None else json.dumps(body)

        # Signed inside the loop, not above it: the signature is valid for 30 seconds from its
        # timestamp, and a retry that waited out a backoff would otherwise present a stale one and
        # come back 401 -- a failure indistinguishable from a bad key, arrived at by retrying.
        retryable = method.upper() == "GET"
        for attempt in range(_MAX_ATTEMPTS):
            timestamp = int(time.time())
            signature = sign_payload(
                self._private_key_b64,
                self._api_key,
                timestamp,
                full_path,
                method.upper(),
                body_str,
            )
            headers = build_headers(self._api_key, signature, timestamp)

            response = requests.request(
                method,
                f"{self._base_url}{full_path}",
                headers=headers,
                data=body_str if body is not None else None,
                timeout=self._timeout,
            )
            if (
                not retryable
                or response.status_code not in _RETRYABLE_STATUSES
                or attempt == _MAX_ATTEMPTS - 1
            ):
                break
            self._sleep(_backoff_seconds(attempt, response.headers.get("Retry-After")))

        if response.status_code == 404:
            return None
        response.raise_for_status()
        if not response.content:
            return None
        # `json.loads(..., parse_float=Decimal)`, never `response.json()`. `requests`' own decoder
        # parses an UNQUOTED JSON number as a `float`, and money fields on this venue DO arrive
        # unquoted. By the time the adapter runs its `Decimal(str(value))` the precision is
        # already gone: it would faithfully preserve whatever the float rounded to, not what
        # Robinhood sent. Converting inside the parser is the only place the original digits still
        # exist.
        #
        # This was a defensive change when it landed (#194 S3) and is no longer.
        #
        # ⚠️ **This venue is NOT internally consistent about quoting** (#217 F6), which is the
        # part worth remembering -- it is not "Robinhood sends numbers":
        #
        #     unquoted: estimated_price.{ask,bid,quantity,fee_ratio,est_fee,est_total_cost}
        #               accounts.fee_tier_status.*
        #               holdings.{total_quantity,quantity_available_for_trading}
        #     quoted:   accounts.buying_power
        #               trading_pairs.{asset_increment,quote_increment,max_order_size}
        #               best_bid_ask.{bid,ask}
        #
        # `accounts` sends `buying_power` quoted and `fee_tier_status.fee_ratio` unquoted in the
        # SAME object. So no field anywhere may be assumed to be one or the other, and no
        # `isinstance` branch is safe: `parse_float=Decimal` here plus `Decimal(str(value))` in
        # the adapter is what makes both forms land on the same exact number, and both halves are
        # required. The fixtures now mirror the venue field for field, so the suite exercises both
        # paths rather than a uniformity that does not exist.
        json_response: Any = json.loads(response.text, parse_float=Decimal)
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
            next_path = self._next_path(_field(page, "next"))
            # After the first page, `next` is already a full absolute URL Robinhood hands back --
            # not a path this transport built -- so params are folded into it already and must
            # not be re-appended on the next iteration.
            next_params = None
        return {"results": results}

    def _next_path(self, cursor: Any) -> str | None:
        """Turn a `next` cursor into a same-host path, or `None` to stop paginating.

        `cursor` is typed `Any` because it comes straight out of a JSON payload, and this method
        exists to stop that `Any` from becoming a crash or, worse, a request to somewhere else.
        Two hazards, both of which are server-controlled input:

        1. **A non-string `next`.** A `null` is already handled as "stop", but a number, a list,
           or an object would previously reach `.startswith` and raise `AttributeError` out of a
           read the adapter has no reason to expect can fail that way. Anything that is not a
           string is treated as "no further pages": one truncated list is a far better outcome
           than an exception surfacing from `get_holdings` mid-reconciliation.
        2. **An absolute URL on a DIFFERENT host.** The old code stripped `self._base_url` only
           when the cursor started with it, and otherwise used the value as a path -- so a
           `next` of `https://evil.example/x` would be concatenated onto `self._base_url` and
           requested as `https://trading.robinhood.com/https://evil.example/x`. That is a
           malformed request rather than a leak, but this transport signs every request with the
           account's credentials, so the rule worth enforcing is simple and absolute: a cursor
           either points at this venue or pagination stops. It is never followed off-host.
        """
        if not isinstance(cursor, str) or not cursor:
            return None
        if cursor.startswith(self._base_url):
            return cursor[len(self._base_url) :]
        if cursor.startswith("/"):
            return cursor
        return None

    def get_accounts(self) -> Any:
        return self._paginate("/api/v2/crypto/trading/accounts/")

    def get_holdings(self) -> Any:
        return self._paginate(
            "/api/v2/crypto/trading/holdings/", params={"account_number": self._account()}
        )

    def get_trading_pairs(self, symbol: str | None = None) -> Any:
        """The venue's per-pair trading rules. **Deliberately not called by the adapter yet.**

        This method and `get_best_bid_ask` are the only two on this transport that `adapter.py`
        never invokes, which is a fair thing to challenge in review, so the reason is written
        down here rather than left to inference.

        They exist because they are the inputs the obvious next feature needs: `asset_increment`,
        `quote_increment`, and `max_order_size` are what would let this package round a size to
        the venue's tick LOCALLY instead of discovering the violation as a rejection.

        ⚠️ **The rows are not all the same shape.** Every row carries `symbol`, `asset_code`,
        `quote_code`, `asset_increment`, `quote_increment`, `max_order_size`, `status` and
        `is_api_tradable`. `min_order_amount` is carried by 63 of the 89 pairs -- BTC-USD (`0.1`)
        and ETH-USD among them -- and absent from the other 26, so anything reading it must treat
        it as optional per pair rather than assume the endpoint is uniform. `min_order_size` does
        not exist at all.

        #217 F3 recorded that no minimum of any kind existed, and #218 removed `min_order_amount`
        from the fixture on that basis. Both were wrong: the probe that produced F3 inspected
        `results[0]` only, and `results[0]` is BILL-USD, one of the 26 (#230). The pre-flight
        minimum-size check proposed in #198 therefore DOES have a lower-bound source for every
        asset keel trades -- with the caveat that it is per pair and may be missing.

        That work is deliberately not done here, and the reason is the same principle
        that shapes `cancel_order` and `_account`: a pre-flight check that runs before every
        placement is also a check that runs before every EXIT, and one that raises -- or merely
        blocks on an extra round trip during an outage -- can trap a position it was meant to
        protect. Sizing validation must therefore be designed to degrade to "place it anyway"
        rather than bolted on as a gate, and that design is a follow-up, not a nit fix.

        They are exercised by the transport tests (endpoint path, signature, response shape), so
        they are not untested code -- only uncalled code, on purpose, with a named successor.
        """
        params = {"symbol": symbol} if symbol is not None else None
        return self._paginate("/api/v2/crypto/trading/trading_pairs/", params=params)

    def get_best_bid_ask(self, symbol: str) -> Any:
        # `marketdata`, not `trading` -- see `get_estimated_price` below for why these two
        # neighbouring endpoints genuinely sit under different namespaces in v2.
        #
        # Rows carry `symbol`, `timestamp`, `bid` and `ask`. Nothing else, and specifically not
        # the `price`, `buy_spread`, `sell_spread`, `ask_inclusive_of_buy_spread` or
        # `bid_inclusive_of_sell_spread` the fixture invented before #217 F4 replaced it -- five
        # keys, none of which this venue sends. No caller reads them today, which is the only
        # reason that cost nothing; the risk was entirely in whatever got written against them
        # next.
        #
        # ⚠️ **`bid` and `ask` are NOT a coherent book snapshot, and may cross** (#413). Measured
        # live on 2026-08-19: BTC-USD and DOGE-USD returned `bid > ask` on every sample, ETH-USD
        # on two of three, XLM-USD and ADA-USD on none. Every crossing was under 1.4 bps, and the
        # pairs that never crossed are the ones whose real spread (2-5 bps) exceeds that -- so the
        # two legs are sampled independently and then stamped with one `timestamp` the row does
        # not actually earn. Anything derived from this endpoint must tolerate a non-positive
        # spread rather than treat it as a venue error, and `translate.to_price_side`'s
        # BUY->`ask` / SELL->`bid` mapping INVERTS on a crossed row: both directions come out
        # optimistic, which is the one outcome that mapping exists to prevent. Nothing calls this
        # method today; #413 owns deciding the contract before anything does.
        return self._paginate(
            "/api/v2/crypto/marketdata/best_bid_ask/", params={"symbol": symbol}
        )

    def get_estimated_price(self, symbol: str, side: str, quantity: str) -> Any:
        # ⚠️ `trading`, NOT `marketdata`. This looks like a copy-paste error next to
        # `get_best_bid_ask` above and it is not -- Robinhood's v2 API really does split these
        # two market-data reads across two namespaces. Verified against the primary source,
        # https://docs.robinhood.com/crypto/trading/, which lists them verbatim as:
        #
        #     get/api/v2/crypto/trading/estimated_price/
        #     get/api/v2/crypto/marketdata/best_bid_ask/
        #
        # The v1 API is the consistent one (`/api/v1/crypto/marketdata/estimated_price/`), which
        # is very likely where the instinct to "fix" this path comes from. Do not change it to
        # `marketdata` on symmetry grounds: a wrong path here is a 404, `_request` turns a 404
        # into `None`, and `_estimated_price` then reports an unpriced preview -- a silent
        # degradation of the confirm gate rather than an error anyone would notice.
        #
        # Required query params, per the same page: `symbol`, `side` (`bid`/`ask`/`both`), and
        # `quantity` -- all three marked required.
        #
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

    def get_orders(self, updated_at_start: str | None = None) -> Any:
        """List this account's orders, newest-first, with every page already concatenated.

        This exists for `adapter.get_fee_summary`, which sums each order's `fee_charged` to
        produce a real `fees_usd` (#197). The v2 order LIST endpoint is the only place that total
        can come from: the API publishes a fee *rate* and a trailing volume at the account level
        and no fees-paid figure anywhere.

        **`updated_at_start` is a real, documented, SERVER-side filter and that is what makes the
        sweep affordable.** https://docs.robinhood.com/crypto/trading/ documents this endpoint
        with `account_number` (required), `cursor`, `created_at_start`, `created_at_end`,
        `updated_at_start`, `updated_at_end`, `symbol`, `side`, `type` and `state`. Without a
        server-side window the caller would have to page the account's ENTIRE order history on
        every fee summary and discard most of it client-side, which against a 100 req/min limit
        and a transport with no backoff is a cost that grows with the account's age forever. With
        it, the sweep is bounded by how much the account traded in the window instead.

        Only the one filter is threaded through, deliberately. Every other documented parameter
        would NARROW the result set, and this is the one caller for whom a narrower set is a
        wrong answer: `state` in particular looks like the obvious filter and would drop a
        partially-filled-then-cancelled order, whose fee was really charged. See
        `adapter._fees_paid` for why `updated_at` rather than `created_at`.

        ⚠️ **Neither this endpoint nor any order object it returns has ever been observed live.**
        `scripts/robinhood_smoke.py` can now probe it read-only, but until an operator with a real
        credential runs that, the envelope shape, the page size, and every field name below the
        `results` key are read from the documentation alone -- the same standing on which the
        `rh_order_*.json` fixtures sit, and the same standing that #217 proved wrong four times
        over on the endpoints that COULD be probed.

        `limit` is not sent: the docs' pagination section says only "some of our endpoints support
        this query parameter" and directs the reader to each endpoint's own parameter list, and
        this endpoint's list does not carry it. An unsupported param is not free here -- it is
        signed, so a guess the venue rejects is a 401 rather than a helpful 400.
        """
        return self._paginate(
            "/api/v2/crypto/trading/orders/",
            params={"account_number": self._account(), "updated_at_start": updated_at_start},
        )

    def get_order(self, order_id: str) -> Any:
        """Fetch one order; `None` only if Robinhood's 404 says this id does not exist.

        See `_request`'s docstring for why every other failure mode raises instead: this is the
        method `adapter.get_order` calls to reconcile a live position, and a `None` here becomes
        a terminal FAILED status one layer up. Reporting FAILED because of a network blip rather
        than because Robinhood actually said "no such order" would tell the engine a resting
        order is gone when it is still live on the venue.

        `account_number` rides on the query string, exactly as `create_order` sends it.
        Robinhood's own v2 sample client (https://docs.robinhood.com/crypto/trading/, "Making
        your first API call") builds this call as:

            params = {"account_number": account_number}
            path = f"/api/v2/crypto/trading/orders/{order_id}/{query_params}"

        Omitting it risks a 404 -- and a 404 on THIS method is the quiet corruption described
        above, not a loud failure: `_request` turns it into `None`, and `adapter.get_order` turns
        that into a terminal FAILED with zeroed money for an order still resting at the venue.
        """
        return self._request(
            "GET",
            f"/api/v2/crypto/trading/orders/{order_id}/",
            params={"account_number": self._account()},
        )

    def cancel_order(self, order_id: str) -> Any:
        """Cancel one order; `None` only if Robinhood's 404 says this id does not exist.

        Same reasoning as `get_order`: a `None` here must mean "the venue has never heard of this
        id," not "something went wrong while cancelling." `adapter.cancel_order` treats anything
        else -- including a successful response whose `state` is not `"canceled"` -- as evidence
        to re-poll, precisely because a resting order that failed to cancel is still live money
        and must never be reported as gone on the strength of a raised exception it didn't get.

        Unlike `create_order` and `get_order`, this endpoint sends NO `account_number`, and that
        asymmetry is deliberate rather than an oversight. Robinhood's v2 reference documents this
        endpoint with a path parameter `id` and no query-parameter section at all --
        `post/api/v2/crypto/trading/orders/{id}/cancel/`, per
        https://docs.robinhood.com/crypto/trading/ --
        and their own v2 sample client builds it as a bare
        `f"/api/v2/crypto/trading/orders/{order_id}/cancel/"` with no params -- while the same
        sample DOES pass `account_number` for placing and fetching. Adding an undocumented param
        here for the sake of looking consistent would be a guess, and every extra byte on the
        query string is also signed, so a guess that the venue rejects is a 401 on the cancel
        path. The order id alone identifies the order.
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
