# Reaching the console from another device

`keel serve` binds loopback. That is the posture, and this document exists so that widening it
is a decision someone made on purpose rather than a flag someone found.

**Status: incomplete. Nothing remote should be exposed yet.** Two of #648's five requirements
are met — the bind is configurable and a reverse proxy's hostname can be expected explicitly —
and three are not. They are named at the bottom, and they are not paperwork.

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

## ⛔ Not done — do not expose the console yet

Three of #648's requirements remain, and each is a real gap rather than a formality:

- **Session tokens over a remote origin.** The token is generated per `keel serve` run and never
  written to disk, which is right for loopback. Nothing yet states its entropy against an
  attacker who can reach the origin from the open internet, and there is no issuance
  rate-limiting or brute-force posture — on loopback there was no attacker to rate-limit.
- **Secure-context re-verification.** The service worker and manifest work today because
  `http://127.0.0.1` is a secure context *by specification*. Over an external origin that
  property comes from HTTPS instead, and the PWA behaviours have to be re-verified there rather
  than assumed from the loopback behaviour.
- **Bind opt-in beyond a mesh address.** `--host` accepts any address, and binding `0.0.0.0`
  currently produces a server that refuses every request — `HostPolicy` then expects
  `Host: 0.0.0.0`, which no browser sends. It fails closed, which is the safe direction, but it
  fails confusingly and needs its own decision rather than this footnote.

Until those land, `--external-host` is the mechanism waiting for the pass, not the pass.
