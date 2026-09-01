"""The layers between a loopback read surface and the rest of the machine (#435).

Each test here names the attack it refuses. A test that only asserted "the happy path works"
would pass just as well against a server with no checks at all.
"""

from __future__ import annotations

import pytest

from keel.web.security import (
    SESSION_COOKIE,
    SESSION_COOKIE_MAX_AGE_SECONDS,
    HostPolicy,
    new_session_token,
    parse_cookie_header,
    session_cookie,
    split_host_header,
    tokens_match,
)


def _cookie_parts(header: str) -> tuple[str, str, dict[str, str]]:
    """`Set-Cookie` split into name, value and a lowercased attribute map.

    Parsed rather than substring-searched, because a substring assertion passes against a header
    that carries the attribute inside the VALUE, against one that spells it twice, and against
    one whose attributes are separated by something a browser will not split on.
    """
    first, _, rest = header.partition(";")
    name, _, value = first.partition("=")
    attributes: dict[str, str] = {}
    for chunk in rest.split(";"):
        if not chunk.strip():
            continue
        key, _, raw = chunk.partition("=")
        attributes[key.strip().lower()] = raw.strip()
    return name.strip(), value.strip(), attributes


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


def test_the_session_cookie_carries_every_attribute_the_model_depends_on() -> None:
    """Four attributes, each refusing a different thing, asserted as a parsed SET.

    `HttpOnly` keeps the token out of `document.cookie` and so out of anything the derived CSRF
    value is written into. `SameSite=Strict` -- and the assertion is that it is `Strict`, not
    merely that the attribute is present, because `Lax` is the plausible edit and `Lax` attaches
    this cookie to a top-level navigation from a hostile page. `Path=/` matches the scope the
    shell has been served under since #540; anything narrower silently un-authorises a deep link.

    And **`Secure` must be ABSENT**, which is the one that looks like a regression and is not:
    `keel serve` speaks plain http, and every browser drops a `Secure` cookie over plain http.
    Adding it would disable the session rather than harden it -- the same trap `SESSION_COOKIE`
    records about the `__Host-` prefix.
    """
    token = new_session_token()
    name, value, attributes = _cookie_parts(session_cookie(token))

    assert name == SESSION_COOKIE
    assert value == token
    assert attributes["path"] == "/"
    assert "httponly" in attributes
    assert attributes["samesite"] == "Strict"
    assert "secure" not in attributes, (
        "a Secure cookie over plain http is dropped by the browser -- this would turn the "
        "session off, not lock it down"
    )
    assert set(attributes) == {"path", "httponly", "samesite", "max-age"}, (
        "an attribute appeared or vanished without this test being asked about it"
    )


def test_the_cookie_outlives_the_browser_and_dies_with_the_run() -> None:
    """#634's whole trade, in one test.

    **The browser half.** Without `Max-Age` this is a session cookie, and closing the browser --
    or a phone evicting the installed console from memory -- throws away a token that is still
    perfectly valid. So `Max-Age` must be present and must be a real, positive number of seconds:
    `Max-Age=0` and a non-numeric value both parse as "delete this cookie now", which is the
    original bug wearing the fix's clothes.

    **The keel half, which is the property #634 refused to sell.** The cookie is only ever a
    carrier for `ServeConfig.token`, and that token is minted fresh per process. So a cookie that
    outlives the run that minted it authenticates nothing: `test_tokens_are_unique_per_call`
    already pins that two runs never share a token, and this asserts the consequence -- two runs
    never hand out the same cookie either. A persisted server-side secret (the option this issue
    considered and declined) is exactly what would make these two cookies equal.
    """
    max_age = _cookie_parts(session_cookie(new_session_token()))[2]["max-age"]
    assert max_age.isdigit(), f"Max-Age must be a whole number of seconds, got {max_age!r}"
    assert int(max_age) > 0, "a Max-Age of zero tells the browser to delete the cookie at once"
    assert int(max_age) == SESSION_COOKIE_MAX_AGE_SECONDS

    first = session_cookie(new_session_token())
    second = session_cookie(new_session_token())
    assert first != second, (
        "two serve runs handed out the same cookie -- the session token is being reused across "
        "processes, which is the persisted-secret design #634 declined"
    )
