/**
 * keel's service worker (#538).
 *
 * ── THE ONE RULE, AND WHY IT IS STRUCTURAL RATHER THAN CAREFUL ──────────────────────────────
 * A PWA that caches financial data is actively dangerous: opening the app to last week's equity
 * styled as current is worse than an error, because an error is visible. So `/api/*` is
 * `NetworkOnly`, "no exceptions" (the design spec's Service worker table).
 *
 * That rule is enforced three times over, and the first one is the reason to trust it:
 *
 *   1. **Scope.** This file is served from `/static/`, so its registration scope is `/static/`
 *      and `/api/*` is OUTSIDE it. A service worker's `fetch` handler is never invoked for a
 *      request outside its own scope -- not "is skipped", not "returns early": the browser does
 *      not consult it at all. No edit to this file can cache an API response, because no edit to
 *      this file can see one.
 *   2. **`PRECACHE`, a closed list.** The only writes to the cache are `addAll(PRECACHE)` at
 *      install. There is no runtime `cache.put`, anywhere, so there is no code path by which a
 *      response fetched later becomes a stored one.
 *   3. **An explicit guard in `fetch`.** Belt and braces for the day #540 moves the shell to `/`
 *      and the scope widens to the whole origin -- at which point rules 1 and 2 stop being the
 *      same protection and this becomes the one that holds. `tests/web/test_service_worker.py`
 *      pins that the guard exists, so it cannot be tidied away as dead code before then.
 *
 * ── THE CACHE NAME IS THE BUILD, AND THAT IS THE SECOND HAZARD ──────────────────────────────
 * `CacheFirst` on the shell means an upgraded engine could otherwise be met by a stale client
 * holding an older contract -- subtler than a stale balance, because everything renders and only
 * the fields are wrong. So the cache name carries the build: `main.js` registers this file as
 * `sw.js?v=<build>`, a different byte sequence for the browser to compare, which is what makes
 * an upgrade trigger an update at all. `activate` then deletes every cache that is not this
 * build's, so an old shell is gone rather than merely unused.
 *
 * ── WHAT THIS BUYS, IN ONE SENTENCE ─────────────────────────────────────────────────────────
 * With `keel serve` stopped, opening the installed app shows the shell and its own banner saying
 * keel is not running -- rather than the browser's dinosaur, or the server's 403 page, which is
 * what the same click gets today. It never shows a figure.
 */

/**
 * This build's cache. Read from the registration URL's query string, which is the only channel a
 * service worker has to its registrant that does not require the page to still be open.
 *
 * `"unknown"` is a real state, not a fallback nobody hits: a registration without `?v=` gets its
 * own cache name and behaves correctly in every other respect. It is what a hand-typed
 * registration in a console would produce, and it must not silently share a cache with a real
 * build.
 */
const BUILD = new URL(self.location.href).searchParams.get("v") || "unknown";
const CACHE = `keel-shell-${BUILD}`;

/** The prefix this worker is allowed to touch, matching its own scope. */
const BASE = "/static/";

/** The path prefix that is never cached, never stored, never served from a cache. */
const API_PREFIX = "/api/";

/** The document every in-scope navigation resolves to -- `staticfiles.CLIENT_ENTRY`. */
const SHELL = `${BASE}index.html`;

/**
 * Everything the app needs to paint with no network.
 *
 * A closed, hand-maintained list rather than a directory walk, because a service worker cannot
 * walk a directory -- and `tests/web/test_service_worker.py` compares this list against the files
 * actually present under `keel/web/static/`, so an asset added without a line here fails the
 * build rather than producing an app that works until it is opened offline.
 */
const PRECACHE = [
  SHELL,
  `${BASE}manifest.webmanifest`,
  `${BASE}css/keel.css`,
  `${BASE}js/api.js`,
  `${BASE}js/chart.js`,
  `${BASE}js/docs.js`,
  `${BASE}js/format.js`,
  `${BASE}js/live.js`,
  `${BASE}js/main.js`,
  `${BASE}js/render.js`,
  `${BASE}icons/keel.svg`,
  `${BASE}icons/keel-192.png`,
  `${BASE}icons/keel-512.png`,
  `${BASE}icons/keel-maskable-512.png`,
];

/**
 * Install: fill this build's cache, then take over immediately.
 *
 * `skipWaiting` rather than waiting for every tab to close, and the version key is what makes
 * that safe: the new worker serves the new build's cache, the old one is deleted in `activate`,
 * and a client that reloads gets a consistent set. Waiting would leave an upgraded engine being
 * read by the previous shell for as long as one tab stayed open -- exactly the failure the
 * version key exists to prevent.
 *
 * `cache: "reload"` on every request: the server sends `Cache-Control: no-store` on static
 * assets (`server._STATIC_BASE_HEADERS`), but the HTTP cache is not the only thing between here
 * and the file, and an install that populated itself from a stale intermediate would bake the
 * staleness in for the life of the build.
 */
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE.map((path) => new Request(path, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

/**
 * Activate: delete every other keel cache, then claim open clients.
 *
 * Scoped to the `keel-shell-` prefix rather than deleting everything `caches.keys()` returns:
 * this origin is keel's alone today, but a worker that deletes caches it did not create is a
 * worker that will one day delete somebody else's.
 */
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith("keel-shell-") && name !== CACHE)
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

/**
 * Fetch: the shell and its assets from this build's cache; everything else untouched.
 *
 * "Untouched" means `respondWith` is never called -- the browser then performs the request
 * exactly as it would with no worker installed, which is the correct behaviour for `/api/*` and
 * for anything this worker has no opinion about. Calling `respondWith(fetch(request))` instead
 * would be a same-behaviour-looking rewrite that quietly drops streaming and changes how a
 * failed request reports itself.
 */
self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Only GET. A service worker sees no POST from this app -- `/setup/*` is a form on a rendered
  // page outside this scope -- but responding from a cache to any non-GET is meaningless, and
  // "meaningless" and "returns the wrong thing" are the same event here.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Rule 3 (see the module comment): never anything under `/api/`. Unreachable while the scope is
  // `/static/`, load-bearing the moment it is not.
  if (url.pathname.startsWith(API_PREFIX)) return;

  if (!url.pathname.startsWith(BASE)) return;

  // A navigation is a request for a VIEW, not for a file: `/static/insights` names no asset, and
  // the server answers it with the shell (`staticfiles.resolve_client_route`). Matching that here
  // is what makes a deep link work with the engine stopped.
  if (request.mode === "navigate") {
    event.respondWith(
      caches.match(SHELL, { cacheName: CACHE }).then((hit) => hit || fetch(request)),
    );
    return;
  }

  // `ignoreSearch`, because `main.js` registers `sw.js?v=...` and a cache-busting query on an
  // asset would otherwise miss a file that is present and identical.
  event.respondWith(
    caches
      .match(url.pathname, { cacheName: CACHE, ignoreSearch: true })
      .then((hit) => hit || fetch(request)),
  );
});
