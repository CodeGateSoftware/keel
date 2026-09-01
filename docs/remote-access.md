# Reaching the console from another device

`keel serve` binds loopback. That is the posture, and this document exists so that widening it
is a decision someone made on purpose rather than a flag someone found.

**Status: incomplete. Nothing remote should be exposed yet.** Four of #648's five requirements
are met. The last one — re-verifying the PWA's secure-context behaviour over a real HTTPS
origin — cannot be done from a checkout, and it is named at the bottom rather than waved past.

## What the defence actually defends

`keel serve` answers on `127.0.0.1`, and a browser treats `http://127.0.0.1` as a secure
context by specification, which is what makes the service worker and the manifest work over
plain HTTP. Binding loopback is not, by itself, protection: any page in the operator's browser
can issue requests to `127.0.0.1`, and an attacker who points `evil.example` at `127.0.0.1`
gets a connection that arrives on loopback with every network-level check satisfied. That is
DNS rebinding, and the `Host:` header is the only place the lie is visible.

So `HostPolicy` refuses any `Host:` the bind does not account for. `evil.example` is refused
even though the packet came from loopback, because the name is not one this server bound.

## Why a tunnel is refused by default, and why that is correct

A Cloudflare Tunnel forwards to loopback and presents the app's **public domain** in `Host:`.
From inside the process that is indistinguishable from the rebinding attempt above: both are
names that resolve to a machine this server did not bind. Nothing in the request tells them
apart, and nothing can — only the operator knows which name is theirs.

That is why the name has to be typed:

```
keel serve --external-host keel.example.com
```

One specific name, repeatable for more than one. Wildcards are refused **when the server
starts**, not per request: `*`, `*.example.com`, `.example.com`, `0.0.0.0`, `::`, `any`, `all`
and the empty string all raise. A wildcard is not a wider expectation, it is the absence of
one — `*` answers `evil.example` exactly as readily as the operator's own domain, which is the
attack itself. A leading-dot suffix is refused for the same reason: it admits every subdomain
an attacker can provision, and a tunnel presents one name.

Naming a host **extends** the defence and never replaces it. Every loopback spelling still
answers, every unlisted name is still refused, and the port must still match — a proxied
request is not exempt from being addressed to this server.

## What each transport actually guarantees

Stated plainly, because "it's encrypted" is not a threat model.

| | what protects the traffic | who can read it | what the operator is trusting |
| :-- | :-- | :-- | :-- |
| **Loopback only** | nothing leaves the machine | anything running as the operator | the machine itself |
| **WireGuard mesh** (Tailscale/Headscale) | end-to-end between the operator's own devices | only those devices | the mesh's coordination server for key distribution — not for content |
| **Cloudflare Tunnel** | TLS from the browser to Cloudflare, then Cloudflare to the machine | **Cloudflare, in the clear at its edge** | Cloudflare with the plaintext of every page and every session token |

The third row is the one that needs saying out loud. Cloudflare terminates TLS. In that mode
the operator is not merely trusting Cloudflare to route traffic; they are handing it the
readable contents of a console that displays positions, balances and attestations. That may be
an acceptable trade for convenience. It is not a neutral one, and a document that let someone
discover it later would have failed.

A mesh has no such property: WireGuard is end-to-end between devices the operator enrolled, and
the coordination server distributes keys without being able to read what they protect.

## The session, once a remote host is configured

**There is no brute-force threat, and saying so is the point.** The token carries 256 bits of
entropy (`secrets.token_urlsafe(32)`), a space of about 1.2 × 10⁷⁷. An attacker managing a
billion guesses a second — which no `http.server` on a laptop will serve — needs on the order of
10⁶⁰ years to cover a meaningful fraction. A rate limiter installed to stop guessing would be
theatre: state, a failure mode, and a false sense that something was closed. The arithmetic is
recorded beside the constant (`TOKEN_ENTROPY_BITS`) so it travels with the claim. If a limiter is
ever added it must be justified by bounding log volume or making probing visible — never by
brute force.

**What a remote origin does change is who can use a token that leaked.** On loopback that
population is software already running as the operator, and no session lifetime helps against
it. Through a tunnel it becomes anyone who can reach the origin — and the token has been in a
URL, in terminal scrollback, and in whatever got pasted while asking for help. The 30-day cookie
`Max-Age` is a *browser* hint that such an attacker ignores entirely.

So a server configured with `--external-host` enforces a **12-hour session lifetime on its own
side of the wire**, checked before the token so an expired session cannot be told apart from a
wrong one by which refusal comes back. A loopback-only server has none, and that asymmetry is
the argument rather than an exemption. Restarting `keel serve` remains the instant revocation
gesture in both postures.

## Binding every interface

`--host 0.0.0.0` (or `::`) used to produce a server that refused **every** request: `HostPolicy`
would then expect `Host: 0.0.0.0`, which no browser sends. It failed closed — the right
direction, the wrong explanation, and the operator's conclusion was "keel is broken" rather than
"keel does not know which name to expect".

A wildcard bind is precisely the case where the name cannot be derived, because every interface
has a different one. So it is the one bind that requires stating it, and `keel serve` now refuses
to start on a wildcard with no `--external-host` — once, at the moment it can be acted on.

## ⛔ Not done — do not expose the console yet

One requirement remains, and it is not a formality:

- **Secure-context re-verification.** The service worker and manifest work today because
  `http://127.0.0.1` is a secure context *by specification*. Over an external origin that
  property comes from HTTPS instead, and the PWA behaviours have to be re-verified there rather
  than assumed from the loopback behaviour. That needs a real deployed origin, so it cannot be
  closed from a checkout — and until it is, an installed console reached through a tunnel is
  untested, not merely unsupported.
