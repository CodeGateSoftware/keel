"""Zero-network tests for `keel_broker_robinhood.transport`'s pure functions.

`sign_payload` and `build_headers` are free functions rather than methods specifically so the
Ed25519 signing rule is unit-testable against a known keypair with no network call at all -- that
design choice is what these tests exist to cash in on. `_field` and `_results` get their own
coverage here because they are the only thing standing between a malformed or paginated response
shape and a `KeyError`/`AttributeError` surfacing on the live-money path.
"""

from __future__ import annotations

import base64
from typing import Any

import nacl.signing
from keel_broker_robinhood.transport import _field, _results, build_headers, sign_payload

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
