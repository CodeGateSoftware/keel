/**
 * keel's service worker (#538).
 *
 * ── THE ONE RULE, AND WHY IT IS STRUCTURAL RATHER THAN CAREFUL ──────────────────────────────
 * A PWA that caches financial data is actively dangerous: opening the app to last week's equity
 * styled as current is worse than an error, because an error is visible. So `/api/*` is
 * `NetworkOnly`, "no exceptions" (the design spec's Service worker table).
 *
 * ── THE SCOPE WIDENED AT #540, AND THE GUARD IS NOW THE ONE THAT HOLDS ──────────────────────
 * Until #540 this file was served from `/static/`, so its registration scope was `/static/` and
 * `/api/*` was OUTSIDE it -- the browser never consulted this worker for an API request at all,
 * and no edit to this file could have cached one. That was a structural guarantee, and it is
 * **gone**: the shell moved to `/`, so the scope is the whole origin and every `/api/*` request
 * now passes through the `fetch` handler below.
 *
 * The rule is unchanged; what enforces it is not. Two things do now:
 *
 *   1. **An explicit guard in `fetch`.** It was written at #538 as dead code, with a test
 *      (`test_the_worker_still_guards_the_api_prefix_explicitly`) whose docstring said it "stops
 *      being redundant on the same day that nobody is thinking about it". This is that day. It
 *      returns without calling `respondWith`, so the browser performs the request exactly as it
 *      would with no worker installed.
 *   2. **`PRECACHE`, a closed list.** The only write to the cache is `addAll(PRECACHE)` at
 *      install. There is no runtime `cache.put`, anywhere, so there is no code path by which a
 *      response fetched later becomes a stored one -- and a test pins that there never is.
 *
 * `Cache-Control: no-store` on every `/api/*` response (`server._API_HEADERS`) is the layer
 * below both, and it is the one that does not depend on this file being correct.
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
const BASE = "/";

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
 * Install: fill this build's cache, and then WAIT.
 *
 * ── THIS CALLED `skipWaiting()` UNCONDITIONALLY, AND THAT WAS WRONG ─────────────────────────
 * The argument was that the version-keyed cache made it safe: the new worker serves the new
 * build's cache, the old one is deleted in `activate`, so nothing stale survives. That reasoning
 * is sound about CACHES and silent about the thing that actually breaks -- the page already on
 * screen. `skipWaiting()` plus `clients.claim()` takes over a document that was parsed and
 * rendered by the OLD build's JavaScript, and every request it makes afterwards is answered from
 * the NEW build's cache. One page, two builds, no indication.
 *
 * keel had a second line of defence that made this hard to notice: a new build means the process
 * restarted, which means a new session token, which means every `/api/*` call from the old page
 * is a 403 the banner reports. So the window was narrow and loud rather than wide and quiet. It
 * was still a window, and "another layer happens to cover it" is not a reason to keep a hazard
 * that costs one message to remove.
 *
 * So the new worker installs its cache and stays in `waiting`. `main.js` notices, tells the
 * operator a new build is ready, and only a click sends `SKIP_WAITING` -- at which point the page
 * reloads into the build it just accepted. Nothing is ever half-upgraded, and the operator finds
 * out an upgrade happened, which they could not before.
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
      .then((cache) => cache.addAll(PRECACHE.map((path) => new Request(path, { cache: "reload" })))),
  );
});

/**
 * The one message this worker answers: "the operator accepted the update, take over".
 *
 * A message rather than a timer or a heuristic, because the decision is not the worker's to make.
 * `skipWaiting()` here is safe in the way it was not in `install`: the page that sent it is about
 * to reload, so there is no document left running the old build for the new one to serve.
 */
self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") void self.skipWaiting();
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

  // Never anything under `/api/`. This was unreachable while the scope was `/static/`; since
  // #540 widened the scope to the whole origin it is the check that keeps a balance out of the
  // cache. See the module comment.
  if (url.pathname.startsWith(API_PREFIX)) return;

  if (!url.pathname.startsWith(BASE)) return;

  // A navigation is a request for a VIEW, not for a file: `/insights` names no asset, and
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
