"""The layers between a loopback read surface and the rest of the machine (#435).

Each test here names the attack it refuses. A test that only asserted "the happy path works"
would pass just as well against a server with no checks at all.
"""

from __future__ import annotations

import pytest

from keel.web.security import (
    HostPolicy,
    new_session_token,
    parse_cookie_header,
    split_host_header,
    tokens_match,
)


def test_a_hostname_that_resolves_to_loopback_is_still_refused() -> None:
    """DNS rebinding, which is the attack loopback binding invites.

    The attacker points `evil.example` at 127.0.0.1, so the connection genuinely arrives on
    loopback and every network-level check passes. The `Host:` header is the only place the lie
    is visible."""
    policy = HostPolicy(bound_host="127.0.0.1", port=8765)
    assert policy.permits("127.0.0.1:8765")
    assert not policy.permits("evil.example:8765")
    assert not policy.permits("evil.example")


def test_a_missing_host_header_is_refused() -> None:
    """HTTP/1.1 requires one. A request without it is hand-written, not from the browser this
    UI exists for -- and defaulting to "allow" would make the whole check optional."""
    policy = HostPolicy(bound_host="127.0.0.1", port=8765)
    assert not policy.permits(None)
    assert not policy.permits("")


def test_the_port_must_match_too() -> None:
    """Another server on this machine, on another port, is not this server."""
    policy = HostPolicy(bound_host="127.0.0.1", port=8765)
    assert not policy.permits("127.0.0.1:9000")


def test_every_loopback_spelling_is_accepted_when_bound_to_loopback() -> None:
    policy = HostPolicy(bound_host="127.0.0.1", port=8765)
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
        assert policy.permits(host), host


def test_an_explicit_non_loopback_bind_accepts_only_that_address() -> None:
    """`--host 10.0.0.5` widens the exposure deliberately; it does not widen it to `localhost`
    or to any hostname that happens to resolve there."""
    policy = HostPolicy(bound_host="10.0.0.5", port=8765)
    assert policy.permits("10.0.0.5:8765")
    assert not policy.permits("localhost:8765")
    assert not policy.permits("evil.example:8765")
    assert not policy.is_loopback


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("127.0.0.1:8765", ("127.0.0.1", "8765")),
        ("localhost", ("localhost", None)),
        ("[::1]:8765", ("[::1]", "8765")),
        ("[::1]", ("[::1]", None)),
        ("[::1", ("[::1", None)),
        ("a:b:c", ("a:b:c", None)),
    ],
)
def test_host_header_splitting(header: str, expected: tuple[str, str | None]) -> None:
    """A bracketed IPv6 literal is the only case that makes this non-trivial, and anything
    malformed must fall through to a value that FAILS the policy rather than one that guesses."""
    assert split_host_header(header) == expected


def test_tokens_match_rejects_absence_and_mismatch() -> None:
    token = new_session_token()
    assert tokens_match(token, token)
    assert not tokens_match(None, token)
    assert not tokens_match("", token)
    assert not tokens_match(token[:-1] + "x", token)


def test_tokens_are_unique_per_call() -> None:
    """Minted per run and never persisted, so stopping the server invalidates it."""
    assert len({new_session_token() for _ in range(50)}) == 50


def test_cookie_parsing_drops_malformed_pairs_rather_than_raising() -> None:
    """A `Cookie:` header is attacker-influenced input. Dropping an unparseable pair leaves the
    request with no session, which the caller refuses -- an exception here would instead be a
    500 on every page for as long as the bad cookie survives in the browser."""
    assert parse_cookie_header(None) == {}
    assert parse_cookie_header("") == {}
    assert parse_cookie_header("keel_session=abc") == {"keel_session": "abc"}
    assert parse_cookie_header('keel_session="abc"; other=1') == {
        "keel_session": "abc",
        "other": "1",
    }
    assert parse_cookie_header("HttpOnly; keel_session=abc") == {"keel_session": "abc"}
