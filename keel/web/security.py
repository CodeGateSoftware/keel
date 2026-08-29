"""What stands between a loopback read surface and the rest of the machine.

A server on `127.0.0.1` is not private just because it is loopback. Three different things can
still reach it: any other process running as this user, any page the user's browser happens to be
displaying, and -- through DNS rebinding -- any site that can point its own hostname at
`127.0.0.1`. What is on the other side is the operator's entire financial position: equity, high
water mark, open positions, every closed trade. So loopback is where the model starts, not what it
is.

Four independent layers, none of which is sufficient alone:

1. **Loopback bind** removes the network. It says nothing about this machine.

2. **`Host` header validation** removes DNS rebinding -- the attack that loopback binding invites
   and that people forget, because the packet really does arrive on loopback and the bind check
   really does pass. The attacker's page resolves `evil.example` to `127.0.0.1`; the browser
   connects to loopback but sends `Host: evil.example`; a server that compares that against the
   address it bound says no. This is the cheapest layer and the one that matters most.

3. **A session token** removes every other local process, and cross-site requests from pages the
   user is already viewing. `keel serve` mints it per run, prints it in the URL, and the browser
   exchanges it for a `SameSite=Strict` cookie on first load. `Strict` (not `Lax`) is deliberate:
   `Lax` attaches the cookie to top-level navigations, so a link on a hostile page would arrive
   authenticated. Nothing is persisted -- close the server and the token is gone.

4. **A closed set of setup actions** is the whole write surface. `POST` exists now (#437 -- a
   first-run user on a machine with no terminal has to be able to create a deployment somehow),
   but it routes ONLY through `keel.commands.setup.ACTIONS`, every member of which is a step
   declared `MECHANICAL`, is idempotent and is never destructive. Not one of the eight
   capability-increasing actions in `keel/capabilities.py` is reachable, and a test asserts the
   two sets are disjoint. That is a narrower guarantee than "no POST at all" was, and a more
   useful one: "no POST" would have been satisfied by a server that could not set anything up,
   while this is satisfied only by one that cannot arm, release or spend anything.

5. **A CSRF token** on every write. The `SameSite=Strict` cookie already stops a cross-site POST
   in any current browser; this is the layer that does not depend on the browser being current.
   It is derived from the session token by HMAC rather than stored, so there is no server-side
   session table to expire, and it is deliberately NOT the session token itself -- that one is
   `HttpOnly` and must never be written into the page.

The token comparison uses `secrets.compare_digest`; a `==` on a secret leaks its prefix through
timing, and the fact that this is loopback traffic makes the measurement *easier*, not harder.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

#: Bytes of entropy behind the session token. 32 bytes is ~43 url-safe characters -- far past
#: anything guessable, and still short enough to survive being pasted into a browser bar by hand.
_TOKEN_BYTES = 32

#: The cookie the token is exchanged for. Prefixed `__Host-` would be stronger still, but that
#: prefix REQUIRES `Secure`, and `Secure` cookies over plain http are dropped by every browser --
#: a name that silently disables the cookie is worse than a plain name that works.
SESSION_COOKIE = "keel_session"

#: The request header carrying the CSRF token on a write (#540).
#:
#: A HEADER rather than a body field, and the difference is the whole reason this layer still
#: earns its place now that the write surface is JSON. The token used to ride in a `<form>` as a
#: hidden input, where its job was to prove the submission came from a page keel rendered. There
#: is no form any more -- so putting it in the JSON body would prove only that the sender could
#: read the token, while putting it in a header ALSO proves the sender could set a header, which
#: a cross-origin form cannot do at all and a cross-origin `fetch` cannot do without surviving a
#: preflight. The same request now clears `X-Keel-Client` and this by the same mechanism, which
#: is redundancy rather than duplication: they fail independently.
CSRF_HEADER = "X-Keel-CSRF"

#: Hostnames that mean "this machine" and are therefore acceptable in a `Host:` header when the
#: server is bound to a loopback address. Anything else -- including a hostname that RESOLVES to
#: 127.0.0.1 -- is rejected, which is the entire point of checking the header at all.
_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def new_session_token() -> str:
    """A fresh token for one `keel serve` run. Never written to disk: a token that outlives the
    process it authorised is a credential, and this package deliberately does not manage any."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


#: Domain separation for the CSRF derivation: this label is what stops the derived value from
#: being usable as, or confusable with, the session token itself.
_CSRF_LABEL = b"keel/web/csrf/v1"


def csrf_token(session_token: str) -> str:
    """The write token for a session, derived rather than stored.

    Derived, so there is no server-side session table to keep, expire or leak, and so it dies
    with the session token it comes from. HMAC rather than a plain hash so that seeing the CSRF
    value -- which is written into the page, unlike the `HttpOnly` cookie -- does not let anyone
    work backwards to the session token."""
    return hmac.new(session_token.encode("utf-8"), _CSRF_LABEL, hashlib.sha256).hexdigest()


def tokens_match(presented: str | None, expected: str) -> bool:
    """Constant-time comparison, tolerant of a missing value."""
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


def split_host_header(value: str) -> tuple[str, str | None]:
    """`"127.0.0.1:8765"` -> `("127.0.0.1", "8765")`; `"[::1]:8765"` -> `("[::1]", "8765")`.

    Hand-written rather than `urllib.parse` because a bracketed IPv6 literal is the only case that
    makes this non-trivial, and because the answer must be a REJECTION on anything surprising --
    a parser that is lenient about malformed input is the wrong shape for a check whose output is
    an authorisation decision."""
    host = value.strip()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return host, None
        literal = host[: end + 1]
        rest = host[end + 1 :]
        if rest.startswith(":"):
            return literal, rest[1:]
        return literal, None
    if host.count(":") == 1:
        name, _, port = host.partition(":")
        return name, port
    return host, None


@dataclass(frozen=True)
class HostPolicy:
    """The `Host:` values this server will answer to, derived from what it actually bound.

    Built from the bind address rather than configured separately, so the policy cannot drift away
    from reality: binding loopback accepts the loopback spellings, and an explicit non-loopback
    bind accepts exactly that address and nothing else."""

    bound_host: str
    port: int

    @property
    def is_loopback(self) -> bool:
        return self.bound_host in _LOOPBACK_NAMES

    def permits(self, host_header: str | None) -> bool:
        """Whether a request carrying this `Host:` may be answered.

        A missing header is refused. HTTP/1.1 requires one, and the only clients that omit it are
        hand-written -- which is to say, not the browser this UI exists for."""
        if not host_header:
            return False
        name, port = split_host_header(host_header)
        if port is not None and port != str(self.port):
            return False
        if self.is_loopback:
            return name in _LOOPBACK_NAMES
        return name == self.bound_host


def parse_cookie_header(value: str | None) -> dict[str, str]:
    """Cookie name/value pairs, ignoring anything malformed.

    `http.cookies.SimpleCookie` would do this, but it raises on input a browser can legitimately
    send (a bare attribute, an unquoted value with a stray character), and a cookie header is
    attacker-influenced input on a page the user might be tricked into loading. Dropping an
    unparseable pair is the correct failure: the request then simply has no session and is refused
    by the layer that cares."""
    out: dict[str, str] = {}
    if not value:
        return out
    for chunk in value.split(";"):
        name, sep, raw = chunk.partition("=")
        if not sep:
            continue
        out[name.strip()] = raw.strip().strip('"')
    return out
