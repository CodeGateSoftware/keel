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
   authenticated. The token is minted per process, so closing the server destroys it and every
   outstanding cookie becomes a string that authenticates nothing.

   **Whether it is written to disk depends on who is watching, and #756 is why.** Attached to a
   terminal, nothing is persisted on this side of the wire -- unchanged. Detached (`launchd`, a
   pipe), `keel/web/runtime.py` records the address in a `0600` file so `keel open` can hand it
   back; read its module docstring before touching that, because the argument is not "it is fine
   to persist a token" but "on that path the token is already in `StandardOutPath` at the daemon's
   umask, and a mode-`0600` file deleted on shutdown is strictly less exposure than the log line
   that would otherwise be the only way in". Since #634 the cookie carries a `Max-Age`
   so the BROWSER stops throwing away a token that is still valid; `SESSION_COOKIE_MAX_AGE_SECONDS`
   carries the whole argument for why that extends convenience and not authority.

4. **A closed set of setup actions** is the whole write surface. `POST` exists now (#437 -- a
   first-run user on a machine with no terminal has to be able to create a deployment somehow),
   but it routes ONLY through `keel.commands.setup.ACTIONS`, every member of which is a step
   declared `MECHANICAL`, is idempotent and is never destructive. Not one of the nine
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
import ipaddress
import secrets
from dataclasses import dataclass

#: Bytes of entropy behind the session token. 32 bytes is ~43 url-safe characters -- far past
#: anything guessable, and still short enough to survive being pasted into a browser bar by hand.
_TOKEN_BYTES = 32

#: The cookie the token is exchanged for. Prefixed `__Host-` would be stronger still, but that
#: prefix REQUIRES `Secure`, and `Secure` cookies over plain http are dropped by every browser --
#: a name that silently disables the cookie is worse than a plain name that works.
SESSION_COOKIE = "keel_session"

#: How long the browser keeps its copy of the session cookie (#634).
#:
#: It used to have no `Max-Age` at all, which made it a SESSION cookie -- and that was never a
#: decision, it was the default. What it cost: closing the browser, or a phone evicting the
#: installed console from memory, threw away a token that was still perfectly valid, and the
#: operator was told to go and read a terminal for a value the browser had just discarded. An
#: installed icon that has to be re-authorised from a terminal is worse than a bookmark.
#:
#: **This does not extend the token's life by one second, and that is the whole argument.** The
#: cookie is checked against `ServeConfig.token`, which `new_session_token` mints per process and
#: which is never written to disk. When `keel serve` exits, every copy of that token -- in a
#: cookie jar, in terminal scrollback, in a link someone pasted -- stops authenticating anything.
#: So a persistent cookie is a browser holding a value whose power is already bounded by a process
#: it does not control, and `new_session_token`'s "a token that outlives the process it authorised
#: is a credential" stays literally true: the token does not outlive the process. Only the
#: browser's copy does, and by then it is inert.
#:
#: **What DOES change, said out loud rather than discovered later.** Closing the browser used to
#: revoke access -- by accident, as a side effect of a default nobody chose. It no longer does.
#: The revocation gesture is restarting `keel serve`, which mints a new token and invalidates
#: every outstanding cookie at once; it is instant, it needs no new command, and it is available
#: at the terminal the operator is already at on the one occasion they care.
#:
#: Thirty days is measured against the useful life of a `keel serve` process, NOT against the
#: value's sensitivity -- the sensitivity ends at process exit whatever this number says. Long
#: enough that the installed icon still works next month; short enough that a browser profile
#: does not accumulate an entry with no end at all.
SESSION_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

#: How long a session may authenticate once a REMOTE origin is configured (#648). Twelve hours:
#: long enough that a working day does not end in a re-authorisation, short enough that a token
#: which leaked this morning does not still work tomorrow.
#:
#: ⚠️ This does NOT apply to a loopback-only server, and the asymmetry is the argument rather
#: than an exemption. `SESSION_COOKIE_MAX_AGE_SECONDS`'s reasoning above is sound and unchanged:
#: the token's power is bounded by a process the browser does not control, and on loopback the
#: population who could USE a leaked copy is "software already running as this operator" --
#: against which a shorter session buys nothing at all.
#:
#: A remote origin changes that population to "anyone who can reach the tunnel", and the token
#: has been in a URL, in terminal scrollback, and in whatever the operator pasted while asking
#: for help. The 30-day cookie is a BROWSER hint an attacker with the token ignores entirely, so
#: the bound has to be enforced on this side of the wire or it is not a bound.
REMOTE_SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

#: ⛔ THE BRUTE-FORCE ARITHMETIC, WRITTEN DOWN SO NOBODY ADDS A RATE LIMITER FOR THE WRONG
#: REASON (#648). `_TOKEN_BYTES = 32` is 256 bits, so `secrets.token_urlsafe` draws from a space
#: of 2**256 ~ 1.2e77. An attacker managing a billion guesses per second -- which no HTTP server
#: on a laptop will serve -- needs on the order of 1e60 years to cover a meaningful fraction.
#:
#: Guessing is therefore NOT a threat this server has, and a rate limiter installed to stop it
#: would be theatre: it would add state, a failure mode, and a false sense that something was
#: closed. What bounds risk here is entropy, which is already past any margin that matters.
#:
#: What a limiter WOULD buy is unrelated to guessing -- bounding log volume from a scanner, and
#: making probing visible. Those are real, and if one is ever added it must be justified by
#: those and not by brute force. Recorded as a constant so the number travels with the claim.
TOKEN_ENTROPY_BITS = 256

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

#: Spellings that mean "stop checking". Refused at construction rather than at request time so a
#: configuration that would disable the defence fails when the server STARTS -- visibly, once --
#: instead of quietly answering everything for as long as it runs.
_WILDCARD_NAMES = frozenset({"*", "", "any", "all", "0.0.0.0", "::", "[::]"})


def _reject_wildcard(name: str) -> None:
    """Refuse an external host that is not one specific name.

    The DNS-rebinding defence works by naming what is expected. A wildcard is not a wider
    expectation, it is the absence of one, and a `*` here would answer `evil.example` exactly as
    readily as the operator's own domain -- which is the whole attack. A leading-dot suffix
    (`.example.com`) is refused for the same reason: it admits every subdomain an attacker can
    provision, and a tunnel presents ONE name.
    """
    cleaned = name.strip().lower()
    if cleaned in _WILDCARD_NAMES or "*" in cleaned or cleaned.startswith("."):
        raise ValueError(
            f"external host {name!r} is a wildcard, not a name. The DNS-rebinding defence works "
            "by naming exactly what is expected; a wildcard removes the check rather than "
            "widening it. List the tunnel's own hostname."
        )


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


#: Domain separation for the GATES derivation (#781). A different label from `_CSRF_LABEL`, and
#: that difference is the whole security property: the setup write token is written into
#: `/api/setup`'s document, so anything that can read that page holds it -- and a value that
#: opened a rail as well as a setup step would make "can create a deployment" and "can release a
#: halt" the same permission. Same secret, two derivations, neither usable as the other.
_GATES_LABEL = b"keel/web/gates/v1"


def gates_token(session_token: str) -> str:
    """The write token for a Tier 1 gated action, derived like `csrf_token` and separately.

    Everything `csrf_token`'s docstring says about derivation applies here: no server-side table
    to keep, expire or leak, and it dies with the session token it comes from, so stopping
    `keel serve` revokes it.

    What it adds is that the two are not interchangeable. `csrf_token` guards
    `keel.commands.setup.ACTIONS` -- idempotent, non-destructive, `MECHANICAL` steps. This guards
    actions that release a rail. Deriving both from one label would mean a single captured value
    covered both, and the two surfaces could never be revoked independently.
    """
    return hmac.new(session_token.encode("utf-8"), _GATES_LABEL, hashlib.sha256).hexdigest()


def is_loopback_peer(peer: object) -> bool:
    """Whether a socket's REMOTE address is this machine.

    **The peer address, never a header.** `Host:` and `X-Forwarded-For:` are claims the client
    writes, and this check exists for exactly the case where the claim is a lie -- an operator
    who has put `keel serve` behind a tunnel, where every request arrives carrying whatever the
    proxy chose to say. `HostPolicy.permits` reads the header and defends a different thing (DNS
    rebinding); this reads the socket and cannot be talked out of its answer.

    **The whole 127.0.0.0/8 block, not one address.** systemd-resolved answers on 127.0.0.53, and
    a check spelled `== "127.0.0.1"` would refuse a local operator on such a host -- a
    false refusal that reads as a broken button. `ipaddress` decides this rather than a string
    comparison, so the block, the IPv6 `::1`, and the IPv4-mapped `::ffff:127.0.0.1` a dual-stack
    bind presents for a v4 client are all handled by one rule.

    **Fails closed.** A peer this cannot parse -- absent, empty, a hostname, a malformed tuple --
    is one it cannot vouch for, and the only safe reading of "I do not know where this came from"
    is "not from here".

    `object` rather than `Any` for the parameter, deliberately: `Any` would let a caller pass
    anything and silently skip every check below, where `object` makes the narrowing explicit and
    mypy enforce it. What arrives here is `BaseHTTPRequestHandler.client_address`, which typeshed
    declares as a 2-tuple and which is a 4-tuple on an IPv6 socket -- so the validation is real,
    not defensive decoration.
    """
    if not isinstance(peer, tuple) or len(peer) < 2:
        return False
    host = peer[0]
    if not isinstance(host, str) or not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped or address).is_loopback)


def gated_action_permitted(peer: object, *, external_hosts: frozenset[str]) -> bool:
    """Whether a Tier 1 gated action may run for this request (#781).

    **Two conditions, and the second is the one that is easy to miss.** The peer must be
    loopback, AND this deployment must be in loopback-only posture.

    The peer check alone is not enough, and a tunnel is why: `cloudflared` runs on THIS MACHINE
    and connects to keel over loopback, so a request that began on the public internet arrives
    with `client_address` of `127.0.0.1`. `is_loopback_peer` answers truthfully and uselessly.
    Anything reading only the peer would let whoever can reach the tunnel release a rail.

    `external_hosts` is how this module already spells "remote posture" -- `session_expired_at`
    turns a never-expiring session into a 30-day one on exactly that field, reasoning that a
    remote origin "changes the population to anyone who can reach the tunnel". The same signal,
    read for the same reason, one decision further along.

    **This is a floor, not the final policy.** It holds until #648 (the remote-exposure security
    pass) and #656 (device pairing) land; relaxing it is their job and belongs in their PR, not
    in a quiet edit here. Until then a tunnelled deployment serves the read surface and refuses
    every gated action, which is the posture that fails safe.
    """
    if external_hosts:
        return False
    return is_loopback_peer(peer)


def tokens_match(presented: str | None, expected: str) -> bool:
    """Constant-time comparison, tolerant of a missing value."""
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


def session_cookie(token: str) -> str:
    """The `Set-Cookie` value for a session hand-off, built in one place.

    One place because every attribute here is load-bearing and none is a default, so a second
    spelling at a call site is a second chance to drop one silently:

      * `HttpOnly` keeps the token out of `document.cookie`, and therefore out of everything the
        derived CSRF value is deliberately written into.
      * `SameSite=Strict`, never `Lax` -- see layer 3 of the module docstring; `Lax` would attach
        this to a top-level navigation from a hostile page.
      * `Path=/` matches the scope the shell has been served under since #540.
      * `Max-Age` is #634, and `SESSION_COOKIE_MAX_AGE_SECONDS` carries its argument.
      * **No `Secure`**, for the reason `SESSION_COOKIE` gives about the `__Host-` prefix: a
        `Secure` cookie over plain http is dropped by every browser, so adding it here would
        disable the session silently rather than harden it.
    """
    return (
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
        f"Max-Age={SESSION_COOKIE_MAX_AGE_SECONDS}"
    )


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
    #: Names a REVERSE PROXY may legitimately present that this server never bound (#648).
    #:
    #: A Cloudflare Tunnel forwards to loopback and passes the app's PUBLIC domain in `Host:`, so
    #: the rebinding check refuses it -- correctly, and for exactly the same reason it refuses
    #: `evil.example`. From inside the process the two are indistinguishable: both are names that
    #: resolve to a machine this server did not bind. Only the OPERATOR can tell them apart, so
    #: only the operator can name one, one at a time, in configuration.
    #:
    #: ⚠️ This EXTENDS the defence and never replaces it. Empty by default; a name is admitted
    #: only by being listed; there is no wildcard and no "any" -- `_reject_wildcard` refuses the
    #: spellings someone reaches for when a specific name is inconvenient. The port check still
    #: applies to a proxied request the same as to a direct one.
    external_hosts: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in self.external_hosts:
            _reject_wildcard(name)

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
        # Checked BEFORE the bind-derived rules, and it changes neither: an allowlisted name is
        # an addition to what the bind permits, so the loopback and explicit-address branches
        # below answer exactly what they answered before this field existed.
        if name.lower() in self.external_hosts:
            return True
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
