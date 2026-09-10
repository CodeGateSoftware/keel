"""The layers between a loopback read surface and the rest of the machine (#435).

Each test here names the attack it refuses. A test that only asserted "the happy path works"
would pass just as well against a server with no checks at all.
"""

from __future__ import annotations

import pytest

from keel.web import security
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


# -- external hosts: a tunnel's own name, and nothing wider (#648) --------------------------------


def test_an_allowlisted_external_host_is_admitted() -> None:
    """A Cloudflare Tunnel forwards to loopback and presents the app's PUBLIC domain.

    From inside the process that is indistinguishable from a rebinding attempt -- both are names
    that resolve to a machine this server did not bind. Only the operator can tell them apart,
    which is why the name has to be configured rather than inferred.
    """
    policy = HostPolicy(
        bound_host="127.0.0.1", port=8765, external_hosts=frozenset({"keel.example.com"})
    )
    assert policy.permits("keel.example.com:8765")
    assert policy.permits("keel.example.com")


def test_allowlisting_one_name_admits_no_other() -> None:
    """**The pin that matters.** The defence is not weakened, it is extended by exactly one name."""
    policy = HostPolicy(
        bound_host="127.0.0.1", port=8765, external_hosts=frozenset({"keel.example.com"})
    )
    assert not policy.permits("evil.example:8765")
    assert not policy.permits("keel.example.com.evil.example:8765")
    assert not policy.permits("sub.keel.example.com:8765")


def test_the_loopback_rules_are_unchanged_by_an_allowlist() -> None:
    """Adding an external name must not disturb what the bind already permitted, in either
    direction: every loopback spelling still answers, and a rebinding attempt still does not."""
    policy = HostPolicy(
        bound_host="127.0.0.1", port=8765, external_hosts=frozenset({"keel.example.com"})
    )
    for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765"):
        assert policy.permits(host), host
    assert not policy.permits("evil.example:8765")
    assert not policy.permits(None)


def test_the_port_check_still_applies_to_a_proxied_request() -> None:
    """A proxied request is not exempt from being addressed to THIS server."""
    policy = HostPolicy(
        bound_host="127.0.0.1", port=8765, external_hosts=frozenset({"keel.example.com"})
    )
    assert not policy.permits("keel.example.com:9000")


def test_an_external_host_is_matched_case_insensitively() -> None:
    """DNS is case-insensitive and a proxy may present any casing. Refusing on case would be a
    defence that fails open in the operator's head -- they configured the name, it looks right,
    and requests are refused for a reason nothing reports."""
    policy = HostPolicy(
        bound_host="127.0.0.1", port=8765, external_hosts=frozenset({"keel.example.com"})
    )
    assert policy.permits("KEEL.Example.COM:8765")


@pytest.mark.parametrize(
    "wildcard", ["*", "*.example.com", ".example.com", "0.0.0.0", "::", "any", "all", ""]
)
def test_a_wildcard_external_host_is_refused_at_construction(wildcard: str) -> None:
    """A wildcard is not a wider expectation -- it is the ABSENCE of one.

    The rebinding defence works by naming what is expected, so `*` would answer `evil.example`
    exactly as readily as the operator's own domain, which is the attack itself. A leading-dot
    suffix is refused for the same reason: it admits every subdomain an attacker can provision,
    and a tunnel presents one name.

    Refused at CONSTRUCTION, not per request, so a configuration that would disable the defence
    fails when the server starts -- visibly, once -- instead of quietly answering everything for
    as long as it runs.
    """
    with pytest.raises(ValueError, match="wildcard"):
        HostPolicy(bound_host="127.0.0.1", port=8765, external_hosts=frozenset({wildcard}))


def test_no_external_hosts_is_the_default() -> None:
    """Loopback-only is the posture, and remaining the posture unless someone types a name is
    the point. A default that admitted anything would make every other test here decoration."""
    assert HostPolicy(bound_host="127.0.0.1", port=8765).external_hosts == frozenset()
    assert not HostPolicy(bound_host="127.0.0.1", port=8765).permits("keel.example.com:8765")


# -- the Tier 1 gate: loopback, and a token that cannot be borrowed (#781) ------------------------
#
# Stage 1 of #781 builds the GATE and wires no action to a rail. The order is deliberate: at no
# point should a half-built surface exist that can reach `disengage_kill_switch`.


def test_the_gates_token_is_not_the_setup_write_token() -> None:
    """**Domain separation, so a token minted for one surface cannot be replayed on the other.**

    `csrf_token` already derives the setup write token from the session token under a label; this
    is the same construction under a different one. Same secret, different derivation, so a value
    captured from the setup page -- which is written into the document, unlike the `HttpOnly`
    cookie -- cannot be presented to a route that releases a rail.

    Asserted as INEQUALITY rather than as a format, because the failure this prevents is the two
    collapsing into one value, which no format check would notice."""
    session = security.new_session_token()

    assert security.gates_token(session) != security.csrf_token(session)
    assert security.gates_token(session) != session, "and neither is the session token itself"


def test_the_gates_token_is_stable_for_a_session_and_dies_with_it() -> None:
    """Derived, not stored: there is no table to expire or leak, and stopping `keel serve`
    invalidates it because the session token it comes from is gone."""
    first, second = security.new_session_token(), security.new_session_token()

    assert security.gates_token(first) == security.gates_token(first), "stable within a session"
    assert security.gates_token(first) != security.gates_token(second), "and not across sessions"


@pytest.mark.parametrize(
    ("peer", "loopback"),
    [
        (("127.0.0.1", 51234), True),
        (("::1", 51234, 0, 0), True),
        (("127.0.0.53", 51234), True),
        (("192.168.1.10", 51234), False),
        (("10.0.0.4", 51234), False),
        (("0.0.0.0", 51234), False),
        (("::ffff:127.0.0.1", 51234, 0, 0), True),
        (("::ffff:192.168.1.10", 51234, 0, 0), False),
        (("2001:db8::1", 51234, 0, 0), False),
    ],
)
def test_a_gated_action_recognises_only_a_loopback_peer(peer: tuple, loopback: bool) -> None:
    """**The PEER address, which is the one thing in a request an attacker cannot choose.**

    `Host:` and `X-Forwarded-For:` are both attacker-controlled through a tunnel -- a header is a
    claim, and this defence exists precisely for the case where the claim is a lie. The socket's
    remote address is not a claim.

    `127.0.0.53` is in the list because loopback is the whole `127.0.0.0/8` block, not one
    address; systemd-resolved uses `.53` and a check written as `== "127.0.0.1"` would refuse a
    legitimate local operator. The IPv4-mapped IPv6 forms are there because a dual-stack bind
    presents `::ffff:127.0.0.1` for a v4 client, and reading that as remote would refuse every
    local request on such a bind."""
    assert security.is_loopback_peer(peer) is loopback


def test_a_missing_or_malformed_peer_is_not_loopback() -> None:
    """Fails CLOSED. A peer this cannot parse is one it cannot vouch for, and the safe reading of
    "I do not know where this came from" is "not from here"."""
    for peer in (None, (), ("",), ("not-an-address", 1), ("127.0.0.1",)):
        assert security.is_loopback_peer(peer) is False, repr(peer)


def test_a_tunnelled_request_is_refused_even_though_its_peer_is_loopback() -> None:
    """**The peer check alone is not enough, and this is the case that proves it.**

    A Cloudflare Tunnel runs `cloudflared` ON THIS MACHINE and connects to keel over loopback. So
    a request that began on the public internet arrives with `client_address` of `127.0.0.1` --
    `is_loopback_peer` says yes, truthfully, and the answer is useless on its own.

    `external_hosts` is how this codebase already spells "remote posture": `session_expired_at`
    switches a session from never-expiring to 30 days on exactly that field, with the argument
    that a remote origin "changes the population to anyone who can reach the tunnel". A gated
    action must read the same signal, or a deployment behind a tunnel would arm autonomy for that
    same population before #648 and #656 land.

    So both, and neither alone."""
    local = ("127.0.0.1", 51234)
    remote = ("192.168.1.10", 51234)
    tunnelled = frozenset({"keel.example.com"})

    assert security.gated_action_permitted(local, external_hosts=frozenset()) is True
    assert security.gated_action_permitted(local, external_hosts=tunnelled) is False, (
        "a loopback peer on a tunnelled deployment is the cloudflared daemon, not the operator"
    )
    assert security.gated_action_permitted(remote, external_hosts=frozenset()) is False
    assert security.gated_action_permitted(remote, external_hosts=tunnelled) is False


def test_the_gate_fails_closed_on_a_peer_it_cannot_read() -> None:
    """Same rule as `is_loopback_peer`, restated at the level that decides: an unparseable peer
    on a loopback-only deployment is still a refusal."""
    assert security.gated_action_permitted(None, external_hosts=frozenset()) is False
