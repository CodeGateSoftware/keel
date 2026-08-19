"""Zero-network tests for `keel_broker_robinhood.transport`'s pure functions.

`sign_payload` and `build_headers` are free functions rather than methods specifically so the
Ed25519 signing rule is unit-testable against a known keypair with no network call at all -- that
design choice is what these tests exist to cash in on. `_field` and `_results` get their own
coverage here because they are the only thing standing between a malformed or paginated response
shape and a `KeyError`/`AttributeError` surfacing on the live-money path.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import nacl.signing
import pytest
from keel_broker_robinhood.transport import (
    _MAX_PAGES,
    RobinhoodTransport,
    _field,
    _results,
    build_headers,
    sign_payload,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """A decoded fixture, for tests that only need a value out of it (an `account_number`).

    Deliberately NOT `parse_float=Decimal`, unlike `tests/broker_robinhood/test_adapter.py`'s
    loader: the results of this one are handed back to `_FakeResponse(payload=...)`, which
    re-serializes them with `json.dumps` -- and `json.dumps` cannot encode a `Decimal`. A test
    that needs the venue's exact digits must use `_fixture_text` instead.
    """
    with (_FIXTURES_DIR / name).open() as f:
        data: dict[str, Any] = json.load(f)
    return data


def _fixture_text(name: str) -> str:
    """A fixture's raw bytes, for replaying through the transport's own decoder.

    `_FakeResponse(text=...)` puts this string where a live `response.text` would be, so
    `_request` parses it with `json.loads(..., parse_float=Decimal)` exactly as it parses the
    venue. That is the only way to assert on the values a live response would actually produce:
    decoding the fixture here and re-encoding it into `payload=` sends every unquoted number
    through a binary `float` first, which is the precise loss `parse_float=Decimal` exists to
    prevent.
    """
    return (_FIXTURES_DIR / name).read_text()


#: Throwaway Ed25519 test seed. Generated once for this file with
#: `nacl.signing.SigningKey.generate()` and pasted here as a literal -- it has never been
#: registered with Robinhood, or with anything else; it exists solely so `sign_payload` has a
#: real keypair to sign against, letting these tests assert the exact canonical string without
#: touching the network or a real credential.
_TEST_SEED_B64 = "2+kuoJa6M34OpLTpnd6zR1eYaS+gmybyB40W27Fk7H0="

_BASE_KWARGS: dict[str, Any] = {
    "private_key_b64": _TEST_SEED_B64,
    "api_key": "rh-api-key-1",
    "timestamp": 1_754_733_600,
    "path": "/api/v2/crypto/trading/orders/",
    "method": "POST",
    "body": '{"symbol":"BTC-USD"}',
}


def _verify_key() -> nacl.signing.VerifyKey:
    signing_key = nacl.signing.SigningKey(base64.b64decode(_TEST_SEED_B64))
    return signing_key.verify_key


def test_sign_payload_verifies_against_the_exact_canonical_message() -> None:
    """The canonical string is `api_key + timestamp + path + method + body`, concatenated with no
    separators and no delimiter between fields. Getting this concatenation wrong is invisible
    until Robinhood rejects every signed request with a generic auth failure -- there is no
    partial-credit response that says which field was misplaced -- so this test pins the exact
    bytes that must be signed, not merely that `sign_payload` returns *something*."""
    signature_b64 = sign_payload(**_BASE_KWARGS)
    signature = base64.b64decode(signature_b64)
    message = (
        f"{_BASE_KWARGS['api_key']}{_BASE_KWARGS['timestamp']}"
        f"{_BASE_KWARGS['path']}{_BASE_KWARGS['method']}{_BASE_KWARGS['body']}"
    ).encode()

    _verify_key().verify(message, signature)  # raises nacl.exceptions.BadSignatureError on fail


def test_sign_payload_changes_when_the_api_key_changes() -> None:
    """If the signature ignored `api_key`, one account's signed request could be replayed under a
    different key -- exactly the property Ed25519-signing every field is meant to prevent."""
    baseline = sign_payload(**_BASE_KWARGS)
    varied = sign_payload(**{**_BASE_KWARGS, "api_key": "rh-api-key-2"})
    assert varied != baseline


def test_sign_payload_changes_when_the_timestamp_changes() -> None:
    """A signature insensitive to `timestamp` would defeat Robinhood's 30-second replay window --
    a captured signed request could be resent indefinitely."""
    baseline = sign_payload(**_BASE_KWARGS)
    varied = sign_payload(**{**_BASE_KWARGS, "timestamp": _BASE_KWARGS["timestamp"] + 1})
    assert varied != baseline


def test_sign_payload_changes_when_the_path_changes() -> None:
    """A signature insensitive to `path` would let a signature minted for one endpoint (or one
    order id's cancel URL) authorize a request against another."""
    baseline = sign_payload(**_BASE_KWARGS)
    varied = sign_payload(**{**_BASE_KWARGS, "path": "/api/v2/crypto/trading/orders/other-id/"})
    assert varied != baseline


def test_sign_payload_changes_when_the_method_changes() -> None:
    """A signature insensitive to `method` would let a signed GET authorize a POST against the
    same path -- turning a read into a write."""
    baseline = sign_payload(**_BASE_KWARGS)
    varied = sign_payload(**{**_BASE_KWARGS, "method": "GET"})
    assert varied != baseline


def test_sign_payload_changes_when_the_body_changes() -> None:
    """A signature insensitive to `body` would let a signature minted for one order body
    authorize placing a different order entirely -- the single most consequential field on this
    list, since it is where size, price, and side live."""
    baseline = sign_payload(**_BASE_KWARGS)
    varied = sign_payload(**{**_BASE_KWARGS, "body": '{"symbol":"ETH-USD"}'})
    assert varied != baseline


def test_build_headers_renders_the_timestamp_as_a_string() -> None:
    """Robinhood reads `x-timestamp` off the wire as a header value, which is always a string --
    handing `requests` an int here would be a silent type mismatch nothing except the live API
    would ever catch."""
    headers = build_headers("rh-api-key-1", "c2lnbmF0dXJl", 1_754_733_600)

    # `Content-Type` rides along with the three auth headers rather than being added at the
    # request site, so there is exactly one place that decides what goes on a Robinhood request.
    # Every call this transport makes is JSON or bodiless, so it is never wrong to send it.
    assert headers == {
        "x-api-key": "rh-api-key-1",
        "x-signature": "c2lnbmF0dXJl",
        "x-timestamp": "1754733600",
        "Content-Type": "application/json",
    }
    assert isinstance(headers["x-timestamp"], str)


def test_field_reads_a_plain_dict() -> None:
    assert _field({"a": 1}, "a") == 1
    assert _field({"a": 1}, "b", "default") == "default"


class _Obj:
    """Stands in for whatever object shape a future JSON client might return instead of a dict --
    `_field` must work against both, since fixtures in this test suite are plain dicts but a real
    client library is free to wrap responses in attribute-bearing objects."""

    def __init__(self, a: int) -> None:
        self.a = a


def test_field_reads_an_object_via_getattr() -> None:
    assert _field(_Obj(a=2), "a") == 2
    assert _field(_Obj(a=2), "missing", "default") == "default"


def test_results_reads_the_paginated_results_list() -> None:
    response = {"next": None, "previous": None, "results": [1, 2, 3]}
    assert _results(response) == [1, 2, 3]


def test_results_tolerates_a_bare_list() -> None:
    """Tests inject plain, unwrapped list fixtures in places; `_results` must not assume every
    caller handed it a paginated envelope."""
    assert _results([1, 2, 3]) == [1, 2, 3]


def test_results_tolerates_none() -> None:
    """A transport with nothing configured for a given call returns `None`. Treating that as an
    empty list rather than raising keeps every caller of `_results` from having to special-case
    an unset fixture or a genuinely empty response."""
    assert _results(None) == []


# ---------------------------------------------------------------------------------------------
# The network-backed transport, exercised against a fake HTTP layer.
#
# `RobinhoodTransport` is the half of this package that actually talks to a live-money venue, and
# it was previously untested end to end -- only the pure helpers above had coverage. That gap is
# what let a wrong endpoint path ship: nothing asserted which URL any method builds. Everything
# below therefore drives the REAL transport and fakes only `requests.request`, so the code under
# test is the code that runs in production, minus the socket.
#
# Robinhood ships no sandbox, so there is no lower-stakes environment to point these at. A real
# network call from this file would be a real order.
# ---------------------------------------------------------------------------------------------

_BASE_URL = "https://trading.robinhood.com"


class _FakeResponse:
    """The slice of `requests.Response` that `_request` actually touches."""

    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str | None = None
    ) -> None:
        self.status_code = status_code
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Error for url")


class _RecordingHTTP:
    """Stands in for `requests.request`, recording every call and replaying canned responses.

    Responses may be a single `_FakeResponse` (returned for every call), a list (returned in
    order, with the last one repeating), or a callable taking `(method, url, data)`.
    """

    def __init__(self, responses: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = responses

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "data": data, "timeout": timeout}
        )
        if callable(self._responses):
            result: _FakeResponse = self._responses(method, url, data)
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

    `transport._request` does `import requests` at call time, so patching the attribute on the
    real module is what the deferred import will see.
    """
    import requests

    def install(responses: Any) -> _RecordingHTTP:
        recorder = _RecordingHTTP(responses)
        monkeypatch.setattr(requests, "request", recorder)
        return recorder

    return install


def _transport(**kwargs: Any) -> RobinhoodTransport:
    """A transport with a real signing key and a pre-set account number.

    `account_number` is supplied by default so a test asserting one endpoint's path is not also
    forced to absorb the `GET /accounts/` resolution round trip. The tests that care about that
    resolution construct their own without it.
    """
    kwargs.setdefault("account_number", "AB1234567890")
    return RobinhoodTransport(
        api_key="rh-api-key-1", private_key_b64=_TEST_SEED_B64, **kwargs
    )


def _path_of(url: str) -> str:
    """The path + query of a recorded URL -- exactly the string that must have been signed."""
    split = urlsplit(url)
    return split.path + (f"?{split.query}" if split.query else "")


def _assert_signature_covers_what_was_sent(call: dict[str, Any]) -> None:
    """Verify the recorded request's signature against the bytes actually put on the wire.

    This is the single most important property in this module and the one with the least
    forgiving failure mode. Robinhood recomputes the signature server-side from the request it
    RECEIVED, so any divergence between the string signed and the string sent is a 401 that is
    indistinguishable from a bad key, a revoked credential, or a stale clock -- there is no error
    body that says "your query string differed". Rather than asserting the two are equal by
    inspection, this re-derives the canonical message from the recorded URL and body and verifies
    it cryptographically, which is the same check Robinhood's server performs.
    """
    headers = call["headers"]
    body_sent = call["data"] or ""
    if isinstance(body_sent, bytes):
        body_sent = body_sent.decode()
    message = (
        f"{headers['x-api-key']}{headers['x-timestamp']}"
        f"{_path_of(call['url'])}{call['method'].upper()}{body_sent}"
    ).encode()
    signature = base64.b64decode(headers["x-signature"])
    _verify_key().verify(message, signature)


def test_request_signs_exactly_the_path_and_body_it_sends(http: Any) -> None:
    """A GET with query params: the signature must cover the query string too."""
    recorder = http(_FakeResponse(payload={"results": []}))
    _transport().get_estimated_price(symbol="BTC-USD", side="ask", quantity="0.1")

    _assert_signature_covers_what_was_sent(recorder.calls[0])


def test_request_signs_the_exact_body_bytes_it_sends(http: Any) -> None:
    """A POST body is serialized ONCE and that same string is both signed and sent.

    Handing `requests` the dict via its `json=` kwarg would re-serialize it, and nothing
    guarantees the re-serialization is byte-identical to what was signed -- a different key order
    or separator spacing is enough to invalidate the signature.
    """
    recorder = http(_FakeResponse(payload={"id": "abc", "state": "open"}))
    body = {"symbol": "BTC-USD", "client_order_id": "c1", "side": "sell", "type": "market"}
    _transport().create_order(body)

    call = recorder.calls[0]
    _assert_signature_covers_what_was_sent(call)
    assert json.loads(call["data"]) == body


def test_request_returns_none_for_a_404_and_raises_for_every_other_error(http: Any) -> None:
    """The 404/everything-else split is the load-bearing safety property of this transport.

    `None` means "the venue does not recognise this id" and NOTHING else, because
    `adapter.get_order` turns a `None` into a terminal `FAILED` status with zeroed money fields.
    If a 500 or a timeout were laundered into `None` too, a transient blip while polling a live
    resting order would report that order as dead -- and the engine could re-enter a position
    whose original order is still working at the venue.
    """
    http(_FakeResponse(404))
    assert _transport().get_order("no-such-id") is None

    for status in (401, 429, 500, 503):
        http(_FakeResponse(status))
        with pytest.raises(RuntimeError):
            _transport().get_order("some-id")


def test_request_returns_none_for_an_empty_body(http: Any) -> None:
    http(_FakeResponse(200, text=""))
    assert _transport().get_order("some-id") is None


def test_request_percent_encodes_query_values_and_signs_the_encoded_form(http: Any) -> None:
    """Query values are percent-encoded, and the encoded string is what gets signed AND sent.

    The hand-rolled `f"{k}={v}"` join this replaces put raw values on the wire. A `+` in a value
    is the sharp edge: it is the URL encoding of a SPACE, so a server decodes `1E+2` as `1E 2` --
    meaning the value Robinhood verifies the signature over and the value it then parses are
    different strings. That is not a theoretical pairing with the `Decimal` rendering bug either;
    `str(Decimal("1E+2"))` is literally `"1E+2"`.

    Byte-identity is the part that must survive the fix: encoding for the wire but signing the
    raw form (or vice versa) would trade a parsing bug for a permanent 401.
    """
    recorder = http(_FakeResponse(payload={"results": []}))
    _transport().get_estimated_price(symbol="BTC-USD", side="ask", quantity="1E+2")

    call = recorder.calls[0]
    assert "quantity=1E%2B2" in call["url"], call["url"]
    # Decoding what was sent must give back the value that was asked for, not a space-mangled one.
    assert dict(parse_qsl(urlsplit(call["url"]).query))["quantity"] == "1E+2"
    _assert_signature_covers_what_was_sent(call)


def test_request_parses_unquoted_json_numbers_as_decimal_never_float(http: Any) -> None:
    """Money must not pass through `float`, even for one instant.

    Every fixture in this repository quotes its numeric values, which is why this went untested:
    with `"price": "65482.30"` the value is already a `str` and `Decimal(str(v))` is exact. But
    nothing in Robinhood's API guarantees quoting, and `response.json()` parses an UNQUOTED
    number as a `float` -- so `65482.30` unquoted becomes a float, and the adapter's
    `Decimal(str(...))` then faithfully preserves whatever the float rounded to. The precision is
    already gone by the time any `Decimal` is constructed.

    `parse_float=Decimal` moves the conversion to the parser, where the original digits are still
    available.
    """
    http(_FakeResponse(text='{"results": [{"price": 65482.30, "quantity": 0.1}]}'))
    response = _transport().get_estimated_price(symbol="BTC-USD", side="ask", quantity="0.1")

    row = _results(response)[0]
    assert isinstance(row["price"], Decimal), f"got {type(row['price'])}"
    assert row["price"] == Decimal("65482.30")
    assert isinstance(row["quantity"], Decimal)
    # The exactness this buys, stated as the property that actually matters.
    assert row["price"] * 3 == Decimal("196446.90")


#: Every transport method, with the EXACT v2 path it must request.
#:
#: Quoted from https://docs.robinhood.com/crypto/trading/ (the single-page v2 reference). These
#: are asserted literally, and that literalness is the point: nothing previously asserted any
#: endpoint path, which is precisely how a wrong namespace could ship unnoticed. Note that
#: `estimated_price` sits under `/trading/` while `best_bid_ask` sits under `/marketdata/` --
#: that asymmetry is REAL in Robinhood's v2 API and is not a bug in this transport (see
#: `RobinhoodTransport.get_estimated_price`'s comment for the verbatim doc lines).
_ENDPOINTS: list[tuple[str, str, str, str]] = [
    ("get_accounts", "GET", "/api/v2/crypto/trading/accounts/", ""),
    (
        "get_holdings",
        "GET",
        "/api/v2/crypto/trading/holdings/",
        "account_number=AB1234567890",
    ),
    ("get_trading_pairs", "GET", "/api/v2/crypto/trading/trading_pairs/", ""),
    ("get_best_bid_ask", "GET", "/api/v2/crypto/marketdata/best_bid_ask/", "symbol=BTC-USD"),
    (
        "get_estimated_price",
        "GET",
        "/api/v2/crypto/trading/estimated_price/",
        "quantity=0.1&side=ask&symbol=BTC-USD",
    ),
    (
        "get_orders",
        "GET",
        "/api/v2/crypto/trading/orders/",
        "account_number=AB1234567890&updated_at_start=2026-07-10T12%3A00%3A00Z",
    ),
    ("create_order", "POST", "/api/v2/crypto/trading/orders/", "account_number=AB1234567890"),
    (
        "get_order",
        "GET",
        "/api/v2/crypto/trading/orders/order-id-1/",
        "account_number=AB1234567890",
    ),
    ("cancel_order", "POST", "/api/v2/crypto/trading/orders/order-id-1/cancel/", ""),
]

_CALL_ARGS: dict[str, dict[str, Any]] = {
    "get_best_bid_ask": {"symbol": "BTC-USD"},
    "get_orders": {"updated_at_start": "2026-07-10T12:00:00Z"},
    "get_estimated_price": {"symbol": "BTC-USD", "side": "ask", "quantity": "0.1"},
    "create_order": {"body": {"symbol": "BTC-USD"}},
    "get_order": {"order_id": "order-id-1"},
    "cancel_order": {"order_id": "order-id-1"},
}


@pytest.mark.parametrize(("method_name", "verb", "path", "query"), _ENDPOINTS)
def test_every_endpoint_requests_its_documented_v2_path(
    http: Any, method_name: str, verb: str, path: str, query: str
) -> None:
    """Pin the literal URL and HTTP verb of every method that talks to the venue.

    A wrong path is not a loud failure. It is a 404, which `_request` converts to `None`, which
    `adapter.get_order` converts into a terminal `FAILED` status with zeroed money for an order
    that is alive and resting at the venue -- corrupting reconciliation rather than crashing.
    """
    recorder = http(_FakeResponse(payload={"results": [], "next": None}))
    getattr(_transport(), method_name)(**_CALL_ARGS.get(method_name, {}))

    call = recorder.calls[0]
    assert call["method"] == verb
    assert call["url"] == f"{_BASE_URL}{path}" + (f"?{query}" if query else "")
    _assert_signature_covers_what_was_sent(call)


def test_get_order_sends_the_account_number_that_create_order_sends(http: Any) -> None:
    """`account_number` is required on v2's order endpoints, and `get_order` must not omit it.

    Robinhood's own v2 sample client builds this path as
    `f"/api/v2/crypto/trading/orders/{order_id}/{query_params}"` with
    `params = {"account_number": account_number}` -- the same query param `create_order` already
    sends. Omitting it risks a 404, and a 404 here is the quiet failure described above: a live
    resting order reported as terminally FAILED with zeroed money, which reconciliation then acts
    on. This test asserts the two endpoints agree rather than trusting them to drift together.
    """
    recorder = http(_FakeResponse(payload={"id": "order-id-1", "state": "open"}))
    transport = _transport()
    transport.create_order({"symbol": "BTC-USD"})
    transport.get_order("order-id-1")

    def account_of(call: dict[str, Any]) -> str | None:
        return dict(parse_qsl(urlsplit(call["url"]).query)).get("account_number")

    created, fetched = recorder.calls[0], recorder.calls[1]
    assert account_of(created) == "AB1234567890"
    assert account_of(fetched) == account_of(created)


def test_get_orders_omits_the_window_filter_entirely_when_none_is_asked_for(http: Any) -> None:
    """An absent `updated_at_start` must vanish from the query string, not ride as `"None"`.

    Every query param is SIGNED, so a stray literal `updated_at_start=None` is not a param the
    venue ignores -- it is either a 400 or, worse, a filter matching nothing, which would empty
    the fee sweep and report `fees_usd` as zero. That is the precise always-passing failure #197
    exists to close, reintroduced through the query string.
    """
    recorder = http(_FakeResponse(payload={"results": [], "next": None}))
    _transport().get_orders()

    assert _path_of(recorder.calls[0]["url"]) == (
        "/api/v2/crypto/trading/orders/?account_number=AB1234567890"
    )
    _assert_signature_covers_what_was_sent(recorder.calls[0])


def test_get_orders_paginates_so_the_fee_sweep_never_sees_a_page_boundary(http: Any) -> None:
    """A truncated order history is a silent under-report of `fees_usd`, so `get_orders` has to
    resolve every page before the adapter sees it -- and a history that refuses to terminate has
    to RAISE rather than hand back what it managed to collect."""
    page_one = _FakeResponse(
        payload={
            "results": [{"id": "o1", "fee_charged": 1.5}],
            "next": f"{_BASE_URL}/api/v2/crypto/trading/orders/?cursor=page2",
        }
    )
    page_two = _FakeResponse(payload={"results": [{"id": "o2", "fee_charged": 0.25}], "next": None})
    http([page_one, page_two])

    rows = _results(_transport().get_orders())

    assert [row["id"] for row in rows] == ["o1", "o2"]


def test_trading_pairs_and_best_bid_ask_surface_their_documented_fields(http: Any) -> None:
    """The two reads the adapter does not call yet still have to return usable rows.

    `get_trading_pairs` and `get_best_bid_ask` are implemented but uninvoked -- see
    `get_trading_pairs`' docstring for why local tick validation is a follow-up rather than
    something to bolt onto the exit path. Uncalled code with unasserted fixtures is how a method
    quietly rots into something that no longer works by the time the feature needing it arrives,
    so this pins the fields that feature will read: `asset_increment` (the rounding unit),
    `max_order_size` (a bound a rejected order would violate), and the bid/ask legs a spread check
    would compare.

    `min_order_amount` is asserted here again after #230. #217 F3 read it off `results[0]` --
    BILL-USD, one of the 26 pairs of 89 that lack the field -- and concluded the venue publishes no
    minimum at all; #218 then removed it from the fixture. The other 63 pairs carry it, BTC-USD
    (`0.1`) and ETH-USD among them, so the pre-flight sizing check proposed in #198 has a real
    lower-bound source for every asset keel trades. `min_order_size` is still absent.

    The fixture's raw bytes are replayed rather than a re-serialized decode of them, so the values
    reach the assertions through the same `json.loads(..., parse_float=Decimal)` the live path
    uses. Round-tripping through `float` on the way in would defeat the exactness being asserted:
    `asset_increment` is `0.00000001` for BTC, the same value that makes `translate._render`
    necessary, and one satoshi is a real order size on this venue rather than a rounding artefact.

    ⚠️ **Both of these endpoints QUOTE their money values, and `estimated_price` does not**
    (#217 F6). That is why each value is put through `Decimal(...)` here rather than compared
    directly: this venue is not internally consistent about quoting, so `str` is what these two
    reads produce today and nothing about the API guarantees it stays that way. The pairing of
    `parse_float=Decimal` in the transport with `Decimal(str(v))` in the adapter is what makes
    both forms land on the same number, and it is the only read in this package that is safe
    against a venue that mixes them.
    """
    http(_FakeResponse(text=_fixture_text("rh_trading_pairs.json")))
    pair = _results(_transport().get_trading_pairs(symbol="BTC-USD"))[0]
    assert pair["symbol"] == "BTC-USD"
    assert isinstance(pair["asset_increment"], str), "this endpoint quotes its numbers -- #217 F6"
    assert Decimal(pair["asset_increment"]) == Decimal("0.00000001")
    assert Decimal(pair["max_order_size"]) > 0
    assert isinstance(pair["min_order_amount"], str), "quoted, like the rest of this endpoint"
    assert Decimal(pair["min_order_amount"]) == Decimal("0.1")
    assert "min_order_size" not in pair

    http(_FakeResponse(text=_fixture_text("rh_best_bid_ask.json")))
    quote_row = _results(_transport().get_best_bid_ask(symbol="BTC-USD"))[0]
    assert quote_row["symbol"] == "BTC-USD"
    assert quote_row["timestamp"], "the venue timestamps every quote row -- #217 F8"
    assert isinstance(quote_row["bid"], str), "this endpoint quotes its numbers -- #217 F6"
    # NOT `bid < ask`. This fixture used to assert that and BTC-USD does not honour it (#413):
    # the two legs are sampled independently, so on a pair whose real spread is under a basis
    # point they arrive out of order. A spread check written against the old assertion would read
    # a negative spread as a venue error on exactly the assets keel trades most.
    assert Decimal(quote_row["bid"]) != Decimal(quote_row["ask"])

    # The contrast, in one place: the same transport, the same decoder, a different endpoint.
    http(_FakeResponse(text=_fixture_text("rh_estimated_price.json")))
    priced = _results(_transport().get_estimated_price("BTC-USD", "ask", "0.001"))[0]
    assert isinstance(priced["ask"], Decimal), "this endpoint does NOT quote its numbers"


# ---------------------------------------------------------------------------------------------
# `_paginate`: following `next` cursors across pages.
# ---------------------------------------------------------------------------------------------

_HOLDINGS_PATH = "/api/v2/crypto/trading/holdings/"


def test_paginate_follows_a_next_cursor_and_concatenates_both_pages(http: Any) -> None:
    """A holdings or orders list that spans two pages must not silently report only page one.

    Robinhood's `next` on page one is a full absolute URL, not a path this transport built, so
    the second request has to be derived from that cursor rather than re-using the first
    request's params. Getting this wrong either drops every row past the first page (an account's
    real holdings under-reported, feeding a wrong balance into risk checks) or -- if the base URL
    is naively concatenated onto the cursor instead of stripped from it -- sends a malformed
    request to `https://trading.robinhood.com/https://trading.robinhood.com/...` that 404s and
    is silently swallowed by `_request`, which reads exactly the same as "only one page existed."
    """
    page1 = {
        "results": [{"id": "h1"}],
        "next": f"{_BASE_URL}{_HOLDINGS_PATH}?cursor=page2",
        "previous": None,
    }
    page2 = {"results": [{"id": "h2"}], "next": None, "previous": f"{_BASE_URL}{_HOLDINGS_PATH}"}
    recorder = http([_FakeResponse(payload=page1), _FakeResponse(payload=page2)])
    transport = _transport()

    response = transport._paginate(_HOLDINGS_PATH, params={"account_number": "AB1234567890"})

    assert _results(response) == [{"id": "h1"}, {"id": "h2"}]
    assert len(recorder.calls) == 2
    assert recorder.calls[0]["url"] == f"{_BASE_URL}{_HOLDINGS_PATH}?account_number=AB1234567890"
    # The second request's URL is the cursor with the base URL stripped and re-applied once --
    # not the base URL doubled, and not the first page's params tacked back on.
    assert recorder.calls[1]["url"] == f"{_BASE_URL}{_HOLDINGS_PATH}?cursor=page2"
    assert "account_number" not in recorder.calls[1]["url"]


def test_paginate_raises_rather_than_loop_forever_on_a_self_referencing_cursor(http: Any) -> None:
    """`next` is server-controlled input, and a Robinhood bug that hands back a cursor pointing at
    itself must not be able to hang this transport indefinitely against a live-money credential.
    A `get_holdings()` or `get_order` poll that never returns would hold the credential's
    request budget hostage and stall whatever reconciliation loop called it -- the `_MAX_PAGES`
    cap exists precisely so that failure mode becomes a loud `RuntimeError` instead of a silent
    hang. This pins that the cap actually fires, and that it fires at the documented bound rather
    than after some larger, undocumented number of requests.
    """
    self_referencing_next = f"{_BASE_URL}{_HOLDINGS_PATH}?cursor=loop"
    page = {"results": [{"id": "h"}], "next": self_referencing_next, "previous": None}
    recorder = http(_FakeResponse(payload=page))
    transport = _transport()

    with pytest.raises(RuntimeError, match="did not terminate"):
        transport._paginate(_HOLDINGS_PATH, params={"account_number": "AB1234567890"})

    assert len(recorder.calls) == _MAX_PAGES


# ---------------------------------------------------------------------------------------------
# `_next_path`: hardening against hostile or degenerate `next` values.
# ---------------------------------------------------------------------------------------------

_HOSTILE_NEXT_CURSORS: list[Any] = [
    pytest.param(12345, id="non-string-int"),
    pytest.param(["not", "a", "string"], id="non-string-list"),
    pytest.param({"not": "a string"}, id="non-string-dict"),
    pytest.param("", id="empty-string"),
    pytest.param("https://evil.example/api/x", id="off-host-absolute-url"),
    pytest.param("api/v2/crypto/trading/holdings/?cursor=2", id="relative-not-absolute-path"),
]


@pytest.mark.parametrize("cursor", _HOSTILE_NEXT_CURSORS)
def test_next_path_stops_cleanly_on_a_hostile_or_degenerate_cursor(
    http: Any, cursor: Any
) -> None:
    """Every one of these cursor shapes is something Robinhood's server -- or a bug in it --
    could hand back, and none of them may be allowed to crash `get_holdings`/`get_order` mid
    reconciliation or, worse, cause this transport to sign and send the account's live credentials
    to a URL on someone else's host. A non-string `next` used to reach `.startswith` and raise
    `AttributeError` straight out of a read the adapter had no reason to expect could fail that
    way; an absolute URL on a different host used to be concatenated onto the base URL and
    requested rather than refused. The safe behavior for all of these is identical: stop
    paginating, return what was already fetched, and never issue the extra request.
    """
    page1 = {"results": [{"id": "h1"}], "next": cursor, "previous": None}
    recorder = http(_FakeResponse(payload=page1))
    transport = _transport()

    response = transport._paginate(_HOLDINGS_PATH, params={"account_number": "AB1234567890"})

    assert _results(response) == [{"id": "h1"}]
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["url"] == f"{_BASE_URL}{_HOLDINGS_PATH}?account_number=AB1234567890"


_GOOD_NEXT_CURSORS: list[Any] = [
    pytest.param(f"{_BASE_URL}{_HOLDINGS_PATH}?cursor=page2", id="full-same-host-absolute-url"),
    pytest.param(f"{_HOLDINGS_PATH}?cursor=page2", id="bare-absolute-path"),
]


@pytest.mark.parametrize("cursor", _GOOD_NEXT_CURSORS)
def test_next_path_follows_the_two_legitimate_cursor_shapes(http: Any, cursor: str) -> None:
    """The hardening in `_next_path` must reject hostile input without becoming so strict that it
    also rejects the two cursor shapes Robinhood actually sends: a full same-host URL and a bare
    `/api/v2/...` path. A regression here would look identical to the `_MAX_PAGES` bug from the
    caller's side -- pagination silently stops one page early -- except it would trigger on every
    real multi-page account instead of only in a server-bug edge case, quietly under-reporting
    holdings or order history on the very first account with more than one page.
    """
    page1 = {"results": [{"id": "h1"}], "next": cursor, "previous": None}
    page2 = {"results": [{"id": "h2"}], "next": None, "previous": None}
    recorder = http([_FakeResponse(payload=page1), _FakeResponse(payload=page2)])
    transport = _transport()

    response = transport._paginate(_HOLDINGS_PATH, params={"account_number": "AB1234567890"})

    assert _results(response) == [{"id": "h1"}, {"id": "h2"}]
    assert len(recorder.calls) == 2
    expected_second_url = cursor if cursor.startswith(_BASE_URL) else f"{_BASE_URL}{cursor}"
    assert recorder.calls[1]["url"] == expected_second_url


# ---------------------------------------------------------------------------------------------
# `_account()`: resolution from `GET /accounts/` and caching.
# ---------------------------------------------------------------------------------------------


def test_account_resolves_from_the_first_accounts_row_and_then_caches_it(http: Any) -> None:
    """When no `account_number` is configured up front, this transport must resolve one exactly
    once and reuse it for the rest of its life. Every write and every account-scoped read signs
    and sends `account_number` on the query string, so re-resolving it on every call would not
    just waste a request -- if the account list ever changed shape between calls (a second account
    added, ordering shifted), a later call could silently start signing and trading against a
    different account number than the one the caller has been observing. Caching after the first
    resolution is what makes "resolve once" also mean "stays fixed for this transport's lifetime."
    """
    accounts_fixture = _load_fixture("rh_accounts.json")
    expected_account_number = accounts_fixture["results"][0]["account_number"]
    recorder = http(_FakeResponse(payload=accounts_fixture))
    transport = _transport(account_number=None)

    resolved = transport._account()

    assert resolved == expected_account_number
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["method"] == "GET"
    assert _path_of(recorder.calls[0]["url"]) == "/api/v2/crypto/trading/accounts/"

    resolved_again = transport._account()

    assert resolved_again == expected_account_number
    # A second call needing the account must not issue a second `/accounts/` request.
    assert len(recorder.calls) == 1


def test_account_raises_when_accounts_endpoint_returns_no_rows(http: Any) -> None:
    """A credential with zero accounts is not a state this transport can trade in, and it must
    fail loudly at resolution time rather than proceed with `account_number=None` (or some other
    falsy placeholder) silently baked into every subsequent signed request -- which would turn
    into a stream of confusing 401s or 404s with no indication the actual problem was an empty
    accounts list.
    """
    http(_FakeResponse(payload={"results": [], "next": None, "previous": None}))
    transport = _transport(account_number=None)

    with pytest.raises(RuntimeError, match="no accounts"):
        transport._account()


def test_account_raises_when_the_first_row_has_no_account_number_field(http: Any) -> None:
    """A malformed or unexpectedly-shaped accounts row must fail resolution outright rather than
    resolve to `None` (via `_field`'s default) and let that flow silently into every subsequent
    query string as the literal string `"None"` -- which would sign and send a syntactically valid
    but semantically meaningless `account_number`, and every trade or holdings read after it would
    fail against an account the venue has never heard of.
    """
    accounts_fixture = _load_fixture("rh_accounts.json")
    first_row = accounts_fixture["results"][0]
    malformed_row = {k: v for k, v in first_row.items() if k != "account_number"}
    http(_FakeResponse(payload={"results": [malformed_row], "next": None, "previous": None}))
    transport = _transport(account_number=None)

    with pytest.raises(RuntimeError, match="account_number"):
        transport._account()
