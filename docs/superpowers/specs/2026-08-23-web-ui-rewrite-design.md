# Technical Specification: keel's web UI

**Date:** 2026-08-23 · **Status:** specification for the target state

This document describes the browser interface keel serves from the operator's own machine: what the
server does, what the client does, and why each decision was made the way it was. It follows the
shape of [youperiod.app's SPECS.md](https://github.com/getify/youperiod.app/blob/main/SPECS.md) and
the decision rules of
[its technical philosophy](https://github.com/getify/youperiod.app/discussions/36).

**It describes a target, not the present.** Today `keel serve` renders HTML in Python
(`keel/web/render.py`, 872 lines) and ships no JavaScript. What follows replaces that layer. Where a
thing already exists it is marked as such, because the existing security model in particular must be
preserved rather than rebuilt.

---

## Philosophy

The rules used to decide everything below. They mirror the reference philosophy, adapted where keel
differs from an app with no engine.

1. **Privacy, security and safety are the most important feature.** keel holds venue credentials and
   places real orders. Every trade-off resolves in favour of a smaller, more auditable surface, even
   at a cost in convenience or development speed.

2. **Complexity is fought at the individual decision level.** A tool or dependency is admitted only
   when omitting it would undermine the principles above — never because it is convenient or
   familiar. §"Dependencies" records the outcome: there are none on the client.

3. **The app runs entirely on the user's device.** No hosted service, no account, no telemetry, no
   analytics. youperiod says "no stateful server"; keel's equivalent is **the server is on your
   device.** Nothing crosses the network but the venue calls the engine already makes.

   Unlike youperiod, keel cannot be client-only: a Python process wakes on a schedule, enforces the
   rails and writes SQLite **with no browser open.** The browser is a window onto that, never the app
   itself, so IndexedDB is not and cannot be the store of record.

4. **Nothing is transpiled, bundled, minified, or obfuscated** — and no source maps, for the reason
   the reference gives: the code running must be byte-identical to the code the user reads in
   devtools. gzip only. The value of view-source is worth more than the value of tooling.

   keel's own auditability claim must stay scoped honestly: the **UI** is fully readable in any
   browser on any device; the **engine** is readable on GitHub and in the wheel. A PyInstaller
   bundle ships `.pyc`, so it does not make the Python readable, and no wording should imply it does.

5. **Offline-capable, performant, and accessible by default.** Semantic HTML, native form elements,
   `aria-live` on regions that change underneath a reader, keyboard paths, and colour contrast that
   is asserted in CI rather than judged by eye. For financial data, "offline" means *the shell loads
   and says the engine is not running* — never a cached balance (§"Service worker").

---

## Server

`keel serve` runs a local HTTP server, defaulting to `127.0.0.1` on port **8765**, and opens the URL
in the user's default browser.

```cmd
keel serve
```

Port 8765 is deliberate: Freqtrade's FreqUI and Jesse's dashboard both sit on 8080, and an operator
running one of them alongside keel should not discover the clash through a bind error. `--host` and
`--port` override; `--no-open` suppresses the browser launch.

The server does **no rendering**. As in the reference, its job is to serve static files with correct
security headers, plus a JSON API over the database the engine writes.

```
launchd ──► keel agent (daily)  ──►  SQLite  ◄──  keel serve  ──►  127.0.0.1:8765
                                                                     │
                                              ┌──────────────────────┴───────────┐
                                              │  GET  /            static assets │
                                              │  GET  /api/*       JSON          │
                                              │  POST /api/*       gated actions │
                                              └──────────────────────────────────┘
```

Static assets and the API share **one origin**. There is consequently no CORS configuration at all,
and they must not be split.

### The app cannot be served from keeltrading.com

Recorded because it is the first thing anyone proposes. keel's data is a SQLite file on the
operator's machine. A page served from `https://keeltrading.com` is a **different origin** and cannot
read it; it could only fetch from the local server, and an HTTPS page fetching `http://127.0.0.1:8765`
is exactly the path browsers are closing — Private Network Access preflights in Chrome, blocked in
Safari. The only exits are a TLS certificate for the local server (needs a tunnel) or moving the data
to a hosted service (a different product).

`http://127.0.0.1` **is** a secure context by specification, so the service worker, the web app
manifest and browser install all work from the local server with no networking decision required.
keeltrading.com hosts the install instructions and the documentation.

**Consequence: iPhone and Android are out of scope.** Excluded by the architecture, not deferred by
preference — there is no keel on iOS, and no tunnel is in scope. The interface is still built
responsive, because desktop windows get dragged narrow and touch devices running a full OS exist.

### Security headers

The server's principal job, as in the reference.

| header | what it prevents |
|---|---|
| `Content-Security-Policy: default-src 'self'` | Any third-party resource. No CDN, no web fonts, no analytics — none are used, and the header makes that checkable rather than promised. |
| `connect-src 'self'` | **The interface is provably incapable of sending positions, equity or trade history anywhere but the local process.** Browser-enforced, one header, verifiable in seconds. Outbound documentation links are navigation, not connections, and are unaffected. |
| `Referrer-Policy: no-referrer` | Leaking the URL to keeltrading.com when a documentation link is followed. The session token rides in the URL until the cookie exchange; modern browsers already send origin-only cross-origin, so exposure is small, but one header closes it. |
| `X-Content-Type-Options: nosniff` | Content-type sniffing vulnerabilities. |
| `Strict-Transport-Security` | Not sent. The origin is loopback HTTP by design; HSTS would be meaningless here and is included in the reference only because that app is served over the public internet. |

Subresource Integrity authorises any inline `<script>` or `<style>` block, as in the reference.

### Existing security layers — preserve, do not rebuild

`keel/web/security.py` already documents five layers. The rewrite inherits all of them.

1. **Loopback binding.** `--host` accepts anything and says loudly what that means: on a non-loopback
   address the page is readable by anyone who can reach the port.
2. **`Host` header validation**, against DNS rebinding — the attack loopback binding invites and that
   people forget, because the packet really does arrive on loopback and the bind check really does
   pass. Described in-source as "the cheapest layer and the one that matters most."
3. **A session token**, minted per run, never written to disk, exchanged on first load for a
   `SameSite=Strict`, `HttpOnly` cookie. `Strict` rather than `Lax` is deliberate: `Lax` attaches the
   cookie to top-level navigations, so a link on a hostile page would arrive authenticated.
4. **A closed action set.** The entire write surface routes through `keel.commands.setup.ACTIONS`,
   every member declared `MECHANICAL`, idempotent and non-destructive, and **a test asserts that set
   is disjoint from the eleven capability-increasing actions in `keel/capabilities.py`.**
5. **A CSRF token on every write**, derived from the session token by HMAC rather than stored — "the
   layer that does not depend on the browser being current." Comparisons use
   `secrets.compare_digest`, because `==` on a secret leaks its prefix through timing, and loopback
   traffic makes that measurement *easier*, not harder.

**Added by this specification, as a third CSRF layer** (`SameSite` has had parser bypasses): every
`POST /api/*` requires a custom request header, `X-Keel-Client: 1`. A custom header forces a CORS
preflight that a hostile origin cannot satisfy — the attack this closes is the HTML form POST, which
is *not* preflighted. Paired with a `Sec-Fetch-Site: same-origin` check, which page JavaScript
cannot forge.

### The capability asymmetry

**A client that hides a button is not a gate.** Capability-increasing actions are refused by the
server, keyed to the session token, with the GUI human gate (#436) in front. The client may be fully
read and modified by its user and still cannot arm a rule, attest an asset, or enable autonomy.

As `security.py` puts it: "no POST at all" would have been satisfied by a server that could not set
anything up; this is satisfied only by one that cannot arm, release or spend anything.

### Dependencies

The reference admits three client libraries — argon2, base64↔ArrayBuffer, IndexedDB normalisation —
each because implementing it incorrectly would undermine the app's own principles.

Applying the same bar to keel, **the client has none**, and the two reasons are structural rather
than fortunate:

- **keel's cryptography is not in the browser.** It lives in Python and the OS keychain. All three of
  the reference's dependencies are browser-crypto concerns keel does not have.
- **The client performs no arithmetic**, so no decimal library is required (§"The data contract").

| need | answer |
|---|---|
| charts | SVG path generation, hand-written |
| live updates | `EventSource` |
| dates | `Intl.DateTimeFormat` |
| routing | History API |
| decimal arithmetic | not required |
| cryptography | Python and the OS keychain |

Any future exception must clear the reference's bar: justified because getting it wrong ourselves
would undermine keel's principles. Convenience does not qualify.

Type checking runs without a build step: `// @ts-check` with JSDoc annotations and `tsc --noEmit` in
CI. Types are checked; nothing is transpiled and nothing shipped is altered.

---

## Client

Plain ES modules, served exactly as authored.

### First run

A user with no prior state reaches the first-run wizard (#437), which presents the ceremony rather
than skipping it: configuration and database creation, credentials captured **via the OS keychain**
rather than a plaintext `.env`, and each attestation as an explicit step with its reasoning shown.

Attestations are **collected, never defaulted** — a Shariah classification is a human-supplied fact
with a required source, and an unsourced attestation is refused exactly like a missing one. Some
obligations cannot be automated at all: disabling venue-side interest accrual happens in the venue's
own dashboard, because that interest is *riba* and no rail can observe it. The wizard shows a
checklist and requires acknowledgement; it cannot verify.

**Paper mode is the default.** It needs no venue credentials, which makes it the only thing a new
user *can* do first, and it is the right default for a tool whose measured result is that no rule
family is net-positive.

### The data contract

The API is a further consumer of the same frozen report dataclasses the console already builds. It
**never computes**; it serialises what `gather_status`, `build_insights_report` and their siblings
already return. `tests/commands/test_console_thinness.py`, which pins that property for the console
and the current web layer, extends to cover it.

Two rules govern the payload.

**Money crosses the wire as strings, never as JSON numbers.** `JSON.parse` yields IEEE-754 doubles,
and keel is `Decimal`-only for precisely this reason. A serialisation test fails the build if any
monetary field emits a JSON number.

**Values arrive presentation-ready.** The client places them; it never derives them. Every figure a
user sees was computed by the Python that holds the rails — which is what makes the invariant
*checkable* rather than merely likely.

```json
{
  "as_of": "2026-08-23T14:32:07Z",
  "engine": "running",
  "equity": { "value": "12345.67", "display": "$12,345.67" },
  "positions": [
    {
      "product_id": "BTC-USD",
      "qty":      { "value": "0.01000000", "display": "0.01 BTC" },
      "notional": { "value": "500.00",     "display": "$500.00" },
      "pnl":      { "value": "-12.34",     "display": "▼ −$12.34", "state": "bad" }
    }
  ]
}
```

`state` carries the semantic outcome so the client never infers one from a sign, and the `display`
glyph means the distinction survives without colour (§"Accessibility").

**Sorting is server-side.** Tables sort by query parameter and Python orders with `Decimal`. On
loopback the round trip is sub-millisecond, so there is nothing to optimise and no client arithmetic
to audit.

*An integer companion field scaled to cents was considered and rejected.* Precision here is
**per-product** — `base_increment` varies by instrument, which is exactly what #514 and #517 were
about — so a fixed 100× scale silently truncates anything finer than a cent, and a 1e8 scale caps a
USD notional near `Number.MAX_SAFE_INTEGER`. Should instant client-side re-sorting ever be wanted,
the field is named `sort`, is a plain JSON number, and is documented as **ordering only — never
displayed, never summed.**

`GET /api/config` exposes the running version. It is consumed by the service worker's cache key and
by outbound documentation links.

### JS file structure

- **main** — the entry point. Attaches event listeners, owns the History API router, and mounts
  views.
- **api** — the single `fetch` wrapper. Adds `X-Keel-Client`, and turns a failed request into the
  explicit *"keel isn't running"* state rather than an empty view.
- **render** — builds DOM from API payloads. Places `display` strings; contains no arithmetic and no
  money formatting, deliberately, so that a reviewer can confirm the absence by reading one file.
- **chart** — SVG path generation for the equity curve.
- **live** — the `EventSource` subscription and its reconnect behaviour.
- **format** — `Intl.DateTimeFormat` wrappers. Dates only; money is formatted server-side.
- **docs** — constructs outbound keeltrading.com links, with the anchor and the `?v=` version.
- **sw** — the service worker.

In **js/external** there is nothing, and that is the intended end state rather than a stage.

### Service worker

Routing is explicit, and the rules exist because a PWA caching financial data is actively dangerous:
opening the app to last week's equity styled as current is worse than an error.

| route | strategy |
|---|---|
| `index.html`, `.js`, `.css`, icons | `CacheFirst` |
| `/api/*` | **`NetworkOnly`, no exceptions** |

If the engine is not running, the shell loads instantly and reports it. It never shows a cached
account balance.

The cache name is keyed to the build version from `/api/config`, so an upgraded engine can never be
met by a stale shell holding an older contract.

### Accessibility

Semantic HTML, native form elements, `aria-live` and `aria-atomic` on status regions that change
underneath a reader, full keyboard paths, and visible focus.

Contrast is **asserted in CI**, not judged by eye. The WCAG formula is about twenty lines of Python
with no dependencies; every foreground/background pair asserts its minimum ratio, so a palette
regression fails the build. This is §2 of the philosophy applied to design: no tooling, no
dependency, just the arithmetic in a readable test.

Measured against the palette in use today (`render.py:41-49`), text contrast is already good — every
foreground passes AA on both surfaces in both themes, and most pass AAA (`fg` on `bg` is 16.50:1
light, 15.06:1 dark). Two failures must be fixed in the rewrite:

**`--good` and `--bad` are the same brightness.** In light mode `#1f5f4f` has relative luminance
0.0904 and `#96322a` has 0.0893 — a delta of 0.0011, a ratio of **1.01:1**. Profit and loss are
separated by hue alone, and `render.py:82` confirms it: `.good` and `.bad` are pure colour classes.
This fails WCAG 1.4.1 *Use of Color*, in an application whose central signal is gain versus loss.
Roughly one in twelve men cannot reliably distinguish them, and greyscale, e-ink and direct sunlight
collapse the distinction entirely.

The fix is the `state` and `display` fields in the data contract: `▲ +2.4%` / `▼ −2.4%`, where glyph
and sign carry the meaning and colour reinforces it. The two luminances are separated as well, and
`--accent` is split from `--good` — they are byte-identical in both themes today, so a link and a
gain currently render alike.

**Form inputs have no visible boundary.** `render.py:96-97` gives `.field input` the page background
with a `--line` border at **1.27:1**, so the border is the only thing marking the control. This fails
WCAG 1.4.11 *Non-text Contrast* (3:1 for component boundaries). Decorative dividers at 1.27:1 are
exempt and stay; form controls are not, and this lands squarely on the first-run wizard, which is
almost entirely forms.

---

## Documentation

**Nothing is fetched, embedded, bundled or cached.** A documentation reference opens
`https://keeltrading.com/en/docs/{slug}/#{anchor}` in a new tab with `rel="noopener"`, landing
directly on the term.

This works with no new infrastructure, verified against the built site:

- `docs/glossary.md` states its own rule — "Each entry is a `## term` heading, a definition, and a
  `Source:` line."
- Astro emits the IDs. `dist/en/docs/glossary/index.html` contains `id="rail"`, `id="attestation"`,
  `id="instrument-attestation"`, `id="kill-switch"`, `id="qabd"`, `id="riba"`.
- `src/pages/en/docs/[slug].astro` renders every pinned document at a stable path.

The anchor contract is therefore **kebab-case the `## term` heading**, covered by a test asserting
that every anchor the app emits exists in the corresponding source document — a renamed heading
upstream would otherwise break a deep link silently.

**`docs/` stays in keel, because keel is the source and the website is the mirror.**
`engine-docs.manifest.json` pins `CodeGateSoftware/keel@main`, and `scripts/fetch-engine-docs.mjs`
declares itself "the only writer of `src/content/engine-docs/`", exiting non-zero if a pinned
document disappears. Deleting `docs/` from keel would fail the website build, loudly, by design.

What changes is that the **application code** stops carrying documentation prose — which also fixes a
bug already in every release. As `keel/commands/help_console.py:138-146` notes in its own docstring,
"an installed deployment has no docs/ checkout, and the help screen renders that notice as its empty
state."

**The reason is structural, and stronger than a packaging oversight.** `uv_build` packages the module
root — `keel/` — and everything under it: 140 entries, the `.py` files plus
`keel/templates/*.yaml`. `docs/` lives at the *repository* root, outside that tree, so no wheel can
carry it.

`pyproject.toml`'s `artifacts = ["keel/templates/*.yaml"]` is **inert on the pinned backend**
(`uv_build>=0.10.4,<0.13.0`), which was measured rather than assumed: building with that list and
building with `artifacts = []` produce wheels whose contents are identical, both including the two
YAML templates. The adjacent source comment — "the wheel otherwise contains only .py files" — is
therefore wrong, and so was an earlier draft of this section that repeated it.

The consequence is that the empty glossary cannot be fixed by adding a glob. Linking out is not the
cheaper option here; it is the only one that reaches an installed deployment.

**Offline is not hedged.** No inline fallback definitions, no cached snapshot. An operator running a
trading engine has network by definition, and the least technology that does the job is the correct
amount.

Links default to `/en/`; the site also builds `fr` and `ar`, and changing the prefix is a one-line
change if the interface is ever localised.

**Version skew is made visible, not eliminated.** Outbound links carry `?v=` from `/api/config`, and
the documentation page shows a banner when that does not match the ref it was built from. Per-version
documentation paths were rejected: `keeltrading.com/en/docs/v0.11.0/glossary#qabd` **404s today**,
since the site builds `dist/en/docs/glossary/` and the manifest pins `main`. Building versioned trees
is work in the other repository plus a retention policy, multiplied across three languages and the
sitemap; a query parameter and a banner cost one small change and no new routes.

---

## What this deletes

| deleted | lines | why |
|---|---|---|
| `render.py`'s HTML generation | 872 | The server no longer renders. |
| `/glossary`, `render_glossary()` | — | A link needs no renderer. |
| `keel/commands/tui.py` + its tests | ~9,432 | Below. |

**The TUI goes, and "but SSH" is not a reason.** keel's own documentation refutes it three ways:
there is no server deployment profile (the four profiles are *trading* profiles — live, paper,
paper-hourly, paper-equities — not topologies); a headless live cycle already **fails closed**,
because confirm mode "waits for a typed `y` at a terminal"
(`docs/operator-runbook.md:365`); and the project is explicit that no remote surface exists at all —
"Notify-only, by design. There is no remote control surface — no command, query or capability
arrives through notifications, ever" (`docs/operator-runbook.md:864`).

Granting a headless host anyway, `ssh -L 8765:127.0.0.1:8765 host` forwards the **web** interface to
a local browser: full interface, encrypted by SSH, still a secure context because it is localhost at
the reading end, and no curses anywhere. `windows-curses` is unmaintained besides. Deleting ~9,400
lines also answers #525 directly — `keel/commands/` is larger than the engine it drives.

**Two conditions on the deletion, and they are the reason it happens last rather than first.**
`tui.py` imports from `keel.commands.{activity,admission,status}` — it is a front-end *over* the
shared report builders, not their owner — so removing it never touches what the web UI reads. But
D2 (#435) requires parity with the TUI's read surface before the write surface widens, and
`tests/commands/test_tui.py` **is the specification of what is being ported**: it records what each
screen shows and when. Second, once the TUI is gone the browser is the only interactive surface, so
anything previously reachable only through it must be reachable through the browser or deliberately
CLI-only — otherwise a non-technical user is stranded at a step that needs a terminal, which would
undo the point of the exercise.

---

## Build order

The sequence matters in two places only: the API's test pins must exist before the HTML ones are
removed, and parity must be reached before the TUI is deleted.

1. **JSON API** — serialise the existing reports; the money-as-strings rule and its test;
   server-side sort; `GET /api/config`; extend `test_console_thinness.py` to the API layer.
2. **Static serving and headers** — the full header set, SRI, and the static assets listed in
   the bundle manifest, so the double-click path works end to end. Assert this against a **built
   wheel**, not against files on disk: `pyproject.toml`'s `artifacts` key is inert on the pinned
   `uv_build` (measured — see § Documentation), so a glob there proves nothing either way.
3. **Client shell** — router, status view, responsive CSS, the palette fixes and the contrast test.
4. **Remaining views** — the other seven routes, SVG charts, `EventSource` updates.
5. **PWA** — manifest, icons, service worker with the routing above.
6. **Documentation** — deep links carrying `?v=`; the anchor-existence test.
7. **Removal** — `render.py`'s HTML, the HTML routes, and `tui.py` with its tests.

## Distribution

The signed desktop bundle is the primary channel: the end state is a double-click that opens a window
with no terminal command. The window is the user's own browser, via `webbrowser.open` as `serve.py`
already does.

*An embedded native window (pywebview or similar) was rejected.* It buys a title bar and costs a
dependency, three platform backends, and the devtools that make §4 of the philosophy checkable by the
user.

## Open questions

1. Do the manifest, icons and service worker ship in the wheel as well, or only in the desktop
   bundle? The static assets themselves ship in both.
2. Will keeltrading.com take the `?v=` banner, and does it want to pin to the latest tag rather than
   `main`?
