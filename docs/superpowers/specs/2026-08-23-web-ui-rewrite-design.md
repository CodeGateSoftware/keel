# Design — The web UI rewrite: a zero-dependency, view-source console

**Status:** proposal · **Date:** 2026-08-23 · **Supersedes:** the HTML-rendering half of D2 (#435)

## The ask

> "Rewrite Keel's UI to be used on mobile, desktop, laptop, or tablet. Easy to navigate for simple
> and newbie users. Strip out all documents from Keel and point the new UI to keeltrading.com
> documents. Technically I propose PWA to avoid stores' fees and the app will be installed from
> keeltrading.com."

Refined across the design conversation to: **keel stays entirely on the user's device, open source
and free, no server reach.** A paid analysis service is explicitly a *different app and repository*
and is out of scope here. The technical philosophy to follow is
[youperiod.app's](https://github.com/getify/youperiod.app/discussions/36).

## The answer in one paragraph

Most of this already shipped. `keel serve` (#435) binds `127.0.0.1:8765`, mints a session token and
renders eight routes; D1, D3, D5 and D6 are closed and only D4 (#437, the first-run wizard) is open.
What is left is one layer: **`keel/web/render.py`'s 872 lines of server-side HTML generation get
deleted, and the local process serves JSON plus static files instead.** The client becomes plain ES
modules — no framework, no bundler, no transpile, no minification, no source maps, and *zero*
third-party JavaScript. Two things make that last claim achievable rather than aspirational: keel's
cryptography lives in Python and the OS keychain, never in the browser (so youperiod's three
dependencies have no analogue here), and **money crosses the wire as pre-formatted strings**, so the
client never performs arithmetic and never needs a decimal library. The one part of the original ask
that cannot be built is installing the app *from* keeltrading.com — that is an origin problem, not a
preference, and §3 records why.

---

## 1. What exists today

| component | lines | state |
|---|---|---|
| `keel/commands/serve.py` | 89 | binds loopback, mints a one-time token, opens the browser |
| `keel/web/server.py` | 639 | 8 GET routes + a POST write surface at `/setup/` (#469) |
| `keel/web/render.py` | 872 | server-side HTML, inline stylesheet, no JavaScript |
| `keel/web/security.py` | 164 | session token |
| `keel/commands/tui.py` | 5,063 | curses TUI (+ 4,369 lines of tests) |

Routes: `/`, `/setup`, `/activity`, `/insights`, `/rules`, `/venues`, `/gates`, `/glossary`.

D-series status: **D1 #434 · D2 #435 · D3 #436 · D5 #438 · D6 #439 all closed. D4 #437 open.**

`render.py`'s own docstring states the invariant this design must preserve:

> "This module is a THIRD renderer over the same reports, never a second place that computes them."

`tests/commands/test_console_thinness.py` pins that property today. It must pin the API layer after.

## 2. The reference, and what actually transfers

youperiod.app is 137 KB total. Its `server.js` is 4,865 bytes and performs **no rendering** — its
entire job is serving static files with correct security headers. The client is ~35 KB of
hand-written ES modules across eight files, one 3.9 KB stylesheet, no build step.

**Transfers directly:** the no-framework rule; the "third-party code needs strong justification, not
preference" bar; no transpiling, bundling, minifying, or source maps; security headers as the
server's main job; offline, performant and accessible by default.

**Does not transfer:** youperiod is client-only because it has *no engine*. Its data is what the user
typed; the browser is the app; IndexedDB is the database. keel is the inverse — a Python process
wakes on a schedule, talks to the venue, enforces the rails and writes SQLite **with no browser
open.** IndexedDB therefore cannot be keel's store of record.

The *guarantee*, however, does transfer intact. youperiod's "no stateful server" means your data
never leaves your device. keel's equivalent is **the server is on your device.**

**Explicitly rejected:** youperiod encrypts everything at rest behind a 12-character passphrase.
keel cannot. The agent runs unattended via launchd; a passphrase-locked database is one nobody can
open at 03:00. Passphrase-at-rest and unattended autonomy are mutually exclusive, and autonomy is the
deliberate choice. The correct analogue is D4's plan: **OS keychain for credentials**, filesystem
permissions for the database, FileVault/BitLocker for at-rest.

## 3. Why the app cannot be installed from keeltrading.com

keel's data is a SQLite file on the operator's machine, written by a local process holding venue
credentials. A page served from `https://keeltrading.com` is a **different origin** and cannot read
it. It could only fetch it from the local server, and an HTTPS page fetching `http://127.0.0.1:8765`
is precisely the path browsers are tightening — Private Network Access preflights in Chrome, blocked
outright in Safari. It is inconsistent today and narrowing.

The only two exits are giving the local server a real TLS certificate (requires a tunnel — excluded
by "no server reach"), or moving the data to a hosted service (a different product, explicitly out of
scope).

**Therefore: the PWA is served by the origin that serves the data, which is keel itself.**
`http://127.0.0.1` **is** a secure context by specification, so a service worker, a web app manifest
and browser install all work there with no networking decision at all. keeltrading.com hosts the
install instructions and the documentation — which is what it already does.

**Consequence, recorded plainly: iPhone and Android are out of scope.** Not deferred by choice —
excluded by the constraint. There is no keel on iOS, and "no server reach" removes the tunnel that
was the only way a phone could have reached the desktop process. The UI is still built responsive,
because desktop windows get dragged narrow, touch devices running a full OS are in scope, and it
costs almost nothing while the CSS is being written anyway.

## 4. Architecture

### 4.1 Process model

```
launchd ──► keel agent (daily)  ──►  SQLite  ◄──  keel serve  ──►  127.0.0.1:8765
                                                                     │
                                              ┌──────────────────────┴───────────┐
                                              │  GET  /            static assets │
                                              │  GET  /api/*       JSON          │
                                              │  POST /api/*       gated actions │
                                              └──────────────────────────────────┘
```

Static assets and the API share **one origin**, so there is no CORS story at all. Do not split them.

### 4.2 The API contract

The API is a *fourth* consumer of the same frozen report dataclasses the console and the TUI already
consume. It never computes; it serialises what `gather_status`, `build_insights_report` and their
siblings already return.

**Money crosses the wire as strings, never as JSON numbers.** `JSON.parse` yields IEEE-754 doubles,
and keel is `Decimal`-only for exactly the reason that matters here. A serialisation test fails the
build if any monetary field emits a JSON number.

**The API emits presentation-ready values.** Not `{"qty": "0.01", "price": "50000"}` for the client
to multiply — `{"notional": "500.00", "notional_display": "$500.00"}`. Every figure a user sees was
computed by the Python that holds the rails. This is what lets `test_console_thinness.py` extend to
the API layer, and it is what makes §4.3's zero-dependency claim possible.

### 4.3 The client

Plain ES modules, served as authored. **No framework, no bundler, no transpile, no minification, no
source maps** — per §4 of the reference philosophy, the code running must be byte-identical to the
code the user reads in devtools. gzip is fine.

**Zero third-party JavaScript.** Applying the reference's own bar — a dependency must be justified
because getting it wrong ourselves would undermine the project's principles — nothing clears it:

| need | answer |
|---|---|
| charts | SVG polyline, hand-rolled (~150 lines) |
| live updates | `EventSource` (SSE) |
| dates | `Intl.DateTimeFormat` |
| routing | History API over one shell |
| decimal arithmetic | **none required** — §4.2 |
| cryptography | Python + OS keychain, never the browser |

**Type checking without a build step:** `// @ts-check` with JSDoc annotations and `tsc --noEmit` in
CI. Types are checked; nothing is transpiled and nothing shipped is altered.

### 4.4 Security headers

The server's principal job, as in the reference.

- `default-src 'self'` — no CDN, no web fonts, no analytics, no third-party anything.
- **`connect-src 'self'`** — the UI is *provably incapable* of sending positions, equity or trade
  history to any origin but the local process. Browser-enforced, one header, verifiable in seconds.
  This is a stronger claim than keel can make today and should be documented as a feature.
- `X-Content-Type-Options: nosniff`; Subresource Integrity on any inline `<script>`.
- Loopback bind and the session token are retained unchanged.

### 4.5 The capability asymmetry

**The client hiding a button is not a gate.** Capability-increasing actions are refused by the API,
keyed to the session token, with D3's human gate (#436) in front. The client may be fully read and
modified by its user and still cannot arm a rule, attest an asset, or enable autonomy.

## 5. Documentation

`keel/commands/help_console.py:138-146` records a bug that is already shipping:

> "an installed deployment has no docs/ checkout, and the help screen renders that notice as its
> empty state"

`pyproject.toml` confirms it — `artifacts = ["keel/templates/*.yaml"]`. The wheel ships `.py` files
and one YAML template. **No `docs/`.** So `/glossary` renders an empty state in every installed
deployment, including the signed bundle D5 produces.

**The pipeline runs the opposite way from the original ask.** keeltrading.com's
`engine-docs.manifest.json` pins `CodeGateSoftware/keel@main`, and `scripts/fetch-engine-docs.mjs`
declares itself *"the only writer of `src/content/engine-docs/`"* and exits non-zero if a pinned
document disappears. **keel's `docs/` is the source; the website is the mirror.** Deleting `docs/`
from keel would fail the website build, loudly, by design.

What ships instead: **nothing is fetched, embedded, bundled or cached. The app links out.** A
documentation reference opens `https://keeltrading.com/en/docs/{slug}/#{anchor}` in a new tab
(`target="_blank" rel="noopener"`), landing directly on the term.

**This works today with no new infrastructure**, verified against the built site:

- `docs/glossary.md` writes each term as a `## term` heading — an explicit rule stated in the file
  itself: *"Each entry is a `## term` heading, a definition, and a `Source:` line."*
- Astro already emits the IDs. `dist/en/docs/glossary/index.html` contains `id="rail"`,
  `id="attestation"`, `id="instrument-attestation"`, `id="kill-switch"`, `id="qabd"`, `id="riba"`.
- `src/pages/en/docs/[slug].astro` renders every pinned document at a stable path.

So the anchor contract is: **kebab-case the `## term` heading.** The only maintenance burden is that
renaming a heading upstream breaks a deep link — acceptable, and cheaply covered by a test asserting
that every anchor the app emits exists in the corresponding source document.

Outbound links are navigation, not fetches, and are therefore unaffected by `connect-src 'self'`.

**Consequences.** The `/glossary` route, `render_glossary()`, and the web layer's use of
`load_glossary()`/`parse_glossary()` are all deleted rather than ported — a link needs no renderer.
`help_console.py`'s glossary reader stays for the TUI, which cannot open an anchor and instead prints
the URL; the shipped-wheel empty state is the same bug there and gets the same fix.

**Offline is not hedged.** No inline fallback definitions, no cached snapshot. An operator running a
trading engine has network by definition, and per §2 of the reference philosophy the least technology
that does the job is the correct amount.

Links default to `/en/`. The site also builds `fr` and `ar`; switching the prefix is a one-line
change if the interface is ever localised, and is not worth anticipating now.

## 6. Accessibility, as an acceptance criterion

Measured against the current palette (`render.py:41-49`). **Text contrast is already good** — every
foreground passes AA on both surfaces in both themes, most pass AAA (`fg` on `bg` 16.50:1 light,
15.06:1 dark; `muted` 5.32:1 / 6.19:1). Two failures, one specific to this being a trading app:

**6.1 — `--good` and `--bad` are the same brightness.** In light mode `#1f5f4f` has relative
luminance 0.0904 and `#96322a` has 0.0893 — a delta of 0.0011, a ratio of **1.01:1**. Profit and loss
are separated by hue alone. `render.py:82` confirms it: `.good { color: var(--good); }` and
`.bad { color: var(--bad); }` are pure colour classes. This fails WCAG 1.4.1 *Use of Color*, and the
practical consequences are that roughly one in twelve men cannot reliably distinguish gains from
losses, and that greyscale, e-ink and direct sunlight collapse the distinction entirely.

*Fix:* never encode P&L in colour alone — `▲ +2.4%` / `▼ −2.4%`, where glyph and sign carry the
meaning and colour reinforces it. Separate the two luminances as well. Split `--accent` from
`--good`, which are byte-identical in both themes today, so a link and a gain no longer render alike.

**6.2 — form inputs have no visible boundary.** `render.py:96-97` gives `.field input` the page
background with a `--line` border at **1.27:1**. The border is the only thing marking the control,
which fails WCAG 1.4.11 *Non-text Contrast* (3:1 for UI component boundaries). Decorative dividers at
1.27:1 are exempt and stay; form controls are not, and this lands squarely on D4's wizard, which is
almost entirely forms.

**6.3 — checked in CI, not by eye.** The WCAG contrast formula is about twenty lines of Python with
no dependencies. Every foreground/background pair asserts its minimum ratio and `good`/`bad` assert a
minimum luminance separation, so a palette regression fails the build. This is §2 of the reference
philosophy applied to design: no tooling, no dependency, just the arithmetic in a readable test.

**6.4 — baseline:** semantic HTML, native form elements, `aria-live`/`aria-atomic` on status regions
that change underneath the reader, full keyboard paths, visible focus.

## 7. Newbie usability is mostly not a front-end problem

Recorded so effort lands where it pays. Today's navigation is eight peers — an operator's mental
model. The levers, in order of impact: **D4's wizard (#437)**, which replaces roughly ten CLI
invocations and hand-edited YAML; **progressive disclosure**, one "everything is fine / here is the
one thing to do" surface with the eight operator views behind a second tier; and **plain language
with working documentation links** (§5). None of the three is improved by the choice of front-end
technology.

## 8. Decisions recorded

1. **The TUI is kept.** It is the only surface that works over SSH on a headless host, and deletion
   is a one-way door. The cost is two front-ends. *This decision is cheap to reverse later and
   expensive to reverse early; #525's concern about `keel/commands/` being larger than the engine is
   better addressed on its own terms.*
2. **Zero third-party JavaScript**, per §4.3. Any future exception requires the reference's bar:
   justified because getting it wrong ourselves would undermine keel's principles.
3. **No build step**, per §4.3 — including no source maps.
4. **Money as strings**, per §4.2.
5. **Mobile phones are out of scope**, per §3.
6. **`docs/` stays in keel**, per §5 — it is the source the website mirrors.
7. **Documentation is linked, never embedded.** Nothing fetched, bundled or cached; the app opens
   `keeltrading.com/en/docs/{slug}/#{anchor}` in a new tab. No offline fallback (§5).

## 9. Non-goals

- Any hosted service, account system, telemetry, or analytics.
- The paid analysis tier — a different app and repository.
- Encrypting the database at rest behind a passphrase (§2).
- Replacing SQLite as the store of record.
- Widening what the browser may *do*; §4.5's asymmetry is preserved exactly.

## 10. Phases

| # | phase | content |
|---|---|---|
| W1 | JSON API | Serialise the existing reports; money-as-strings rule and its test; extend `test_console_thinness.py` to the API layer |
| W2 | Static serving + headers | §4.4 in full, SRI, asset packaging into the wheel and the bundle |
| W3 | Client shell | Nav, status view, responsive CSS, palette fixes and the contrast test (§6) |
| W4 | Remaining views | The other seven routes, SVG charts, SSE live updates |
| W5 | PWA | Manifest, icons, service worker — **shell only, never data** (§11) |
| W6 | Documentation | Deep links to keeltrading.com; delete `/glossary` and `render_glossary()`; anchor-existence test |
| W7 | Removal | Delete `render.py`'s HTML generation and retire the HTML routes |

## 11. Risks

- **A service worker caching data would be actively dangerous** — opening the app to last week's
  equity styled as current is worse than an error. Cache the shell only; when the engine is not
  running, render *"keel isn't running"*, never a stale figure.
- **Deleting `render.py` breaks the existing thinness pins.** W1 must land the API pins before W7
  removes the HTML ones.
- **The signed bundle must include the static assets**; `pyproject.toml`'s `artifacts` list currently
  covers only the YAML template.
- **A no-build client has no compile-time safety net** by default; mitigated by `tsc --noEmit` (§4.3).
- **Distribution tension.** §4 of the reference philosophy prizes view-source auditability, but D5
  ships a PyInstaller bundle containing `.pyc`, not readable `.py`. Under this philosophy the
  `curl | bash` wheel installer (#479) is the *more* aligned channel. Worth deciding consciously
  rather than by default; the web UI is fully auditable in-browser under either.
- **Version skew, and it is now the main residual risk of linking out.** The website pins `main`
  while an operator runs a tagged release, so a deep link can land on documentation describing
  behaviour their build does not have. Cheapest mitigation is for the docs pages to state which ref
  they were built from; pinning the site to the latest tag instead of `main` is the fuller fix and
  is a website decision, not this one.
- **A renamed heading breaks a deep link silently.** Covered by the anchor-existence test in §5.

## 12. Open questions

1. Do the manifest, icons and service worker ship in the wheel, or only in the desktop bundle?
2. §11's distribution tension: is the signed bundle still the primary channel?

*Resolved during design:* whether to bundle a rendered documentation snapshot for offline use —
**no.** Link out only (§5, decision 7).
