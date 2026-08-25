// @ts-check

/**
 * The entry point: event listeners, the History API router, and mounting views.
 *
 * ── THE ROUTER IS THE HISTORY API AND NOTHING ELSE ───────────────────────────────────────────
 *
 * `pushState` on a click, `popstate` on back and forward, `location.pathname` as the single
 * source of truth for which view is showing. There is no route table with patterns, no
 * parameters, no nested routes and no hash: there are seven names, and `ROUTES` below is the
 * whole thing.
 *
 * *Hash routing (`#/status`) was the alternative, and it is what a client served from a static
 * directory usually reaches for*, because a hash needs no server cooperation at all: every deep
 * link is a request for the same file. It is rejected because it puts a `#` in every URL an
 * operator copies, and because the cooperation it avoids is fifteen lines --
 * `staticfiles.resolve_client_route`, which serves `index.html` for the seven names in
 * `CLIENT_ROUTES` and 404s everything else. That function is also exactly what step 7 of the
 * spec's build order needs anyway, when this shell moves to `/`.
 *
 * ── WHY THE PREFIX IS `/static/` TODAY ───────────────────────────────────────────────────────
 *
 * `/` and the seven paths beside it are still rendered in Python (`server.ROUTES`) and are
 * deleted at #540, not here. So this shell mounts under the static prefix, and `BASE` is the one
 * place in the JavaScript that says so. `index.html`'s hrefs and `staticfiles.STATIC_PREFIX` are
 * the other two places it is spelled;
 * `tests/web/test_client_assets.py::test_the_mount_prefix_is_spelled_the_same_everywhere` pins
 * that the three agree, so #540's move is an edit rather than a hunt.
 *
 * A `<base href>` would have collapsed those three into one. It is not available: `_STATIC_CSP`
 * sets `base-uri 'none'`, and an injected `<base>` retargeting every relative URL on the page is
 * precisely what that directive exists to stop. Absolute hrefs are the cost of that defence, and
 * it is worth paying.
 */

import { read } from "./api.js";
import { indexUrl, rememberVersion } from "./docs.js";
import { available, subscribe } from "./live.js";
import {
  activityView,
  buildLine,
  engineBanner,
  gatesView,
  insightsView,
  rulesView,
  setupView,
  statusView,
  stoppedView,
  venuesView,
} from "./render.js";

/**
 * Where this client is mounted. Becomes `"/"` at #540. See the module note above.
 * @type {string}
 */
const BASE = "/static/";

/**
 * One route.
 * @typedef {object} Route
 * @property {string} name        the path segment under `BASE`, and the nav link's target.
 * @property {string} label       what the nav and `<title>` call it.
 * @property {string[]} endpoints the `/api/*` endpoints this view reads, PRIMARY FIRST.
 */

/**
 * The seven views, in `render.py::NAV`'s order -- status first, "because the status page is the
 * answer to 'is it alive'", as that table's own comment puts it.
 *
 * **`/glossary` is deliberately absent, and that is the spec's decision rather than an
 * oversight.** `api.py`'s route table records it: glossary "gets no counterpart: it becomes an
 * outbound keeltrading.com link in #539 and `render_glossary` is deleted in #540, so an
 * `/api/glossary` would be a surface built in order to be removed". A client route for it would
 * be the same surface one layer up. That makes seven views here against the eight entries in
 * `render.py::NAV`, and the eighth arrives in #539 as a link, not a view.
 *
 * **`endpoints` is a list because one view reads two endpoints.** `/insights` is served by
 * `/api/insights` and `/api/journal`, and that split is `api.py`'s own decision rather than a
 * shape this client chose: "one sortable collection per endpoint keeps `?sort=` unambiguous
 * without a `?table=` beside it, and it gives the journal somewhere to carry its own `?limit=`".
 * The rendered `/insights` page shows both under one heading, and so does this -- both endpoints
 * name `/insights` as their `html_route`, which is the server saying the same thing.
 *
 * The FIRST endpoint is the primary: it supplies the engine banner, the `sort` echo the view's
 * main table renders headers from, and the `data === null` that means "keel isn't running". A
 * secondary read that fails leaves the view standing with a stated gap in it rather than taking
 * the whole page down -- see `mount`.
 *
 * @type {Route[]}
 */
const ROUTES = [
  { name: "status", label: "Status", endpoints: ["status"] },
  { name: "setup", label: "Setup", endpoints: ["setup"] },
  { name: "activity", label: "Activity", endpoints: ["activity"] },
  { name: "insights", label: "Insights", endpoints: ["insights", "journal"] },
  { name: "rules", label: "Rules", endpoints: ["rules"] },
  { name: "venues", label: "Venues", endpoints: ["venues"] },
  { name: "gates", label: "Gates", endpoints: ["gates"] },
];

/** Where an unrecognised path lands, and what `BASE` alone means. @type {Route} */
const DEFAULT_ROUTE = ROUTES[0];

/**
 * How often the page re-reads, in milliseconds.
 *
 * 15 seconds, matching `server.py::_REFRESH_SEC` -- the interval the rendered pages already use
 * for their `<meta http-equiv="refresh">`. Kept identical rather than tuned so that the two
 * front-ends do not disagree about how fresh "fresh" is while both exist.
 *
 * **It survives #537 rather than being replaced by the event stream, and the two do different
 * jobs.** A tick says "something was written" and is what triggers a rebuild; this poll is what
 * re-reads `as_of` so the banner keeps proving the page is in touch with the server, and what
 * keeps the figures moving at all in a browser where the subscription never started. It is also
 * the belt to `events.revision`'s stated blind spot -- a SQLite write that lands in the `-wal`
 * file without touching `keel.db` moves no marker, and fifteen seconds is the ceiling on how long
 * that can go unnoticed.
 *
 * @type {number}
 */
const POLL_MS = 15000;

/** @type {HTMLElement} */
const viewNode = must("view");
/** @type {HTMLElement} */
const engineNode = must("engine");
/** @type {HTMLElement} */
const contentNode = must("content");
/** @type {HTMLElement} */
const buildNode = must("build");
/** The header's outbound documentation link (#539); its href gains `?v=` once the build is known. */
const docsNode = /** @type {HTMLAnchorElement} */ (must("docs-link"));

/**
 * An element that `index.html` guarantees. Throwing beats rendering half a page: the two files
 * ship together in the same directory, so a missing id is a bug in this commit, not a
 * possibility to degrade gracefully around.
 *
 * @param {string} id
 * @returns {HTMLElement}
 */
function must(id) {
  const node = document.getElementById(id);
  if (!node) throw new Error("index.html is missing #".concat(id));
  return node;
}

/**
 * The route a pathname names.
 *
 * `BASE`, `BASE + "index.html"` and anything unrecognised all resolve to the default. An unknown
 * name resolving to Status rather than to a "no such view" page is a deliberate choice for a
 * seven-route client with no user-generated URLs: the only ways to reach one are a typo or a
 * link written before a rename, and in both cases the landing page is more use than an error.
 *
 * @param {string} pathname
 * @returns {Route}
 */
function routeFor(pathname) {
  if (!pathname.startsWith(BASE)) return DEFAULT_ROUTE;
  const name = pathname.slice(BASE.length);
  if (name === "" || name === "index.html") return DEFAULT_ROUTE;
  return ROUTES.find((route) => route.name === name) ?? DEFAULT_ROUTE;
}

/** The path a route lives at. @param {Route} route @returns {string} */
function pathFor(route) {
  return BASE.concat(route.name);
}

/**
 * Which route is on screen. Read by the paint step so that a response arriving after the user
 * has navigated away is dropped instead of painted over the view they are now looking at --
 * the failure this page would otherwise show once per slow report build.
 *
 * @type {Route}
 */
let current = DEFAULT_ROUTE;

/** The poll timer, so a route change can cancel the one it is replacing. @type {number} */
let timer = 0;

/**
 * The query each endpoint is currently being asked with.
 *
 * Sorting and the activity scope are SERVER-SIDE, which is the spec's decision rather than a
 * shortcut: "Tables sort by query parameter and Python orders with `Decimal`. On loopback the
 * round trip is sub-millisecond, so there is nothing to optimise and no client arithmetic to
 * audit." Pressing a header sets a parameter here and re-reads; it never touches a row.
 *
 * **Keyed by ENDPOINT, not by route, and that is what makes `/insights` work.** That view reads
 * two endpoints with two sortable collections, and `api._sort_request` refuses a column an
 * endpoint does not have -- so one query bag per view would send `sort=expectancy` to
 * `/api/journal` and 400 the journal for the crime of the rule table being sorted. One bag per
 * endpoint gives each table its own ordering and makes that class of mistake unreachable.
 *
 * State survives navigation, so walking to another view and back finds the ordering you left. It
 * is deliberately NOT in the URL: a `?sort=` in the address bar is a second source of truth about
 * which view is showing, and `routeFor` would then have to parse one.
 *
 * @type {Map<string, Record<string, string>>}
 */
const params = new Map();

/** The query for one endpoint, creating an empty one on first use. @param {string} endpoint */
function paramsFor(endpoint) {
  const existing = params.get(endpoint);
  if (existing) return existing;
  /** @type {Record<string, string>} */
  const fresh = {};
  params.set(endpoint, fresh);
  return fresh;
}

/**
 * A sort handler for one endpoint: record the column, re-read, repaint.
 *
 * @param {Route} route
 * @param {string} endpoint
 * @returns {(column: string, direction: string) => void}
 */
function sorter(route, endpoint) {
  return (column, direction) => {
    const query = paramsFor(endpoint);
    query.sort = column;
    query.dir = direction;
    void paint(route, true, true);
  };
}

/**
 * The last revision marker the event stream reported, or `""` before the first tick.
 *
 * Compared with `!==` and never parsed. `events.revision`'s only contract is that it CHANGES when
 * something has been written; reading anything else into it would be this client inventing a
 * meaning the server did not give it.
 *
 * @type {string}
 */
let revision = "";

/**
 * Whether a rebuild is waiting for the reader to stop touching the view. See `rebuildInto`.
 * @type {boolean}
 */
let deferred = false;

/**
 * Whether the last read of the current view's own endpoint succeeded.
 *
 * **This exists because the first browser run of #537 showed the banner and the view disagreeing.**
 * `/api/insights` answered 500 ("that report could not be built"), the view rendered the failure,
 * and a tick arriving a moment later repainted the banner green with "keel is set up on this
 * machine" -- both statements true, about different questions, and the one on screen decided by
 * whichever wrote last. A dashboard whose headline contradicts its body is worse than one with no
 * headline.
 *
 * So a NORMAL tick paints the banner only while the view's own read is healthy. A tick that
 * reports a LOSS OF CONTACT always paints, whatever the view is showing: losing the server is
 * strictly more alarming than any report that failed to build, and "a dropped connection is
 * visible, not silent" is the acceptance criterion this whole subscription exists for.
 *
 * @type {boolean}
 */
let reachable = true;

/**
 * Replace the view, without yanking the focus out from under someone.
 *
 * `replaceChildren` destroys the focused element if focus is inside `#content` -- a table's
 * scroll region, an open `<details>`, a sort button someone is tabbing along -- and the browser
 * drops focus to `<body>`, losing a keyboard user's place with no warning and no way back. A
 * dashboard that does that every time the engine writes a row is unusable by keyboard.
 *
 * Two different situations, and the first browser run of #537 proved they need different answers:
 *
 *   * **A rebuild nobody asked for** -- the poll, or a tick reporting that something changed --
 *     is DEFERRED while focus is inside the view, and `focusout` below replays it the moment
 *     focus leaves. Nothing is lost by waiting: the banner keeps updating throughout, so the page
 *     never claims to be fresher than it is, and the deferred paint reads again rather than
 *     replaying a stale fragment.
 *
 *   * **A rebuild the reader just asked for** -- pressing a sort header, changing the scope --
 *     must happen NOW. Deferring it was the first implementation and it is a plain bug: the click
 *     focuses the button, the button is inside the view, so the sort was postponed until focus
 *     left. Pressing a control and having nothing happen is indistinguishable from a broken
 *     control. Caught by clicking one in a real browser, not by any test here.
 *
 * A forced rebuild restores focus to the control that caused it, found again by its `data-focus`
 * key. Without that, sorting a column by keyboard would sort the table and then drop the reader
 * at the top of the document -- which is the same loss of place this function exists to prevent,
 * arrived at from the other direction.
 *
 * @param {Node} view
 * @param {boolean} force  `true` when a reader's own action caused this.
 */
function rebuildInto(view, force) {
  const active = document.activeElement;
  const inside = contentNode.contains(active);
  if (inside && !force) {
    deferred = true;
    return;
  }
  const key = inside && active instanceof HTMLElement ? active.getAttribute("data-focus") : null;

  // Which disclosures were open. `<details>` keeps its state in the DOM and nowhere else, so a
  // rebuild collapses every one of them -- a reader who expanded a cycle to read its events would
  // watch it shut the moment the engine wrote a row. Carried across by the id `render.js` gives
  // each summary, which is derived from the row it describes and is therefore stable across a
  // re-read that did not change that row.
  const open = new Set();
  for (const node of contentNode.querySelectorAll("details[open] > summary[id]")) {
    open.add(node.id);
  }

  deferred = false;
  contentNode.replaceChildren(view);

  for (const node of contentNode.querySelectorAll("details > summary[id]")) {
    if (open.has(node.id) && node.parentElement instanceof HTMLDetailsElement) {
      node.parentElement.open = true;
    }
  }

  if (!key) return;
  const again = contentNode.querySelector('[data-focus="'.concat(key, '"]'));
  if (again instanceof HTMLElement) again.focus();
}

/**
 * Build the view for a route from its readings.
 *
 * A SECONDARY endpoint that failed yields `null` here rather than an exception, and the view
 * decides what to say about it -- `insightsView` renders "the journal could not be read" under
 * its own heading and keeps the track records above it. The alternative, failing the whole view,
 * would let one unbuildable report hide six that built fine.
 *
 * @param {Route} route
 * @param {import("./api.js").Reading[]} readings  one per endpoint, in `route.endpoints`' order.
 * @returns {Node}
 */
function mount(route, readings) {
  const primary = readings[0];
  const data = primary.data;
  const onSort = sorter(route, route.endpoints[0]);

  if (route.name === "setup") return setupView(data);
  if (route.name === "activity") {
    return activityView(data, primary.sort, onSort, (scope) => {
      paramsFor(route.endpoints[0]).scope = scope;
      void paint(route, true, true);
    });
  }
  if (route.name === "insights") {
    const journal = readings[1];
    return insightsView(
      data,
      journal ? journal.data : null,
      primary.sort,
      onSort,
      journal ? journal.sort : null,
      sorter(route, route.endpoints[1]),
    );
  }
  if (route.name === "rules") return rulesView(data, primary.sort, onSort);
  if (route.name === "venues") return venuesView(data, primary.sort, onSort);
  if (route.name === "gates") return gatesView(data);
  return statusView(data);
}

/**
 * Read the current route's endpoints and paint the result.
 *
 * The reads run CONCURRENTLY. `/api/insights` and `/api/journal` each build a status report of
 * their own on the way in, and issuing them one after the other would double the wait for a view
 * that shows them side by side; the server is threaded and both are reads.
 *
 * @param {Route} route
 * @param {boolean} rebuild  `true` to replace the view, `false` to refresh the banner only.
 * @param {boolean} [force]  `true` when a reader's own action asked for this -- see `rebuildInto`.
 */
async function paint(route, rebuild, force) {
  const readings = await Promise.all(
    // Each endpoint with ITS OWN query -- see `params`. `?sort=` names a column of one endpoint's
    // one sortable collection, and `api._sort_request` refuses a column the endpoint does not
    // have, so a shared query would 400 the journal for the crime of the rule table being sorted.
    route.endpoints.map((endpoint) => read(endpoint, paramsFor(endpoint))),
  );
  // The user navigated while this was in flight. Drop it: the banner belongs to the route that
  // asked for it, and painting it now would report the wrong endpoint's engine state.
  if (route !== current) return;

  const primary = readings[0];
  reachable = primary.error === null;
  engineBanner(engineNode, primary);

  if (!rebuild) return;

  if (primary.data === null) {
    // `data: null` is the ONLY route into this view, and `payload.envelope` guarantees the key
    // is `null` rather than `{}` for exactly this reason -- see `render.stoppedView`.
    rebuildInto(stoppedView(primary, pathFor(SETUP_ROUTE)), Boolean(force));
  } else {
    rebuildInto(mount(route, readings), Boolean(force));
  }
  contentNode.setAttribute("aria-busy", "false");
}

/** Where "set keel up on this machine" points. @type {Route} */
const SETUP_ROUTE = ROUTES.find((route) => route.name === "setup") ?? DEFAULT_ROUTE;

/**
 * Show a route: update the nav, the title, the focus, and start its poll.
 *
 * `focus` is `false` on the very first paint and `true` on every navigation. That distinction is
 * the whole of single-page-app focus management: a normal page load leaves focus at the top of
 * the document and a screen reader announces the page, but `pushState` changes nothing a reader
 * can perceive -- focus stays on the link that was just activated, which is now in a nav
 * pointing at content the reader has not been told about. Moving focus to `<main>` (which is
 * `tabindex="-1"` so it can receive it without becoming a tab stop) restores what the browser
 * would have done.
 *
 * @param {Route} route
 * @param {boolean} focus
 */
function show(route, focus) {
  current = route;
  document.title = "keel — ".concat(route.label);

  for (const link of document.querySelectorAll("header nav a")) {
    const isCurrent = link.getAttribute("href") === pathFor(route);
    // `aria-current="page"` is both the assistive signal and the CSS hook (`header
    // a[aria-current="page"]`), so the underline a sighted user sees and the word a reader hears
    // come from one attribute and cannot drift apart.
    if (isCurrent) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }

  contentNode.setAttribute("aria-busy", "true");
  if (focus) viewNode.focus();

  window.clearInterval(timer);
  timer = window.setInterval(() => {
    // A hidden tab is a tab nobody is reading. Skipping the read while hidden keeps a forgotten
    // background tab from building a status report every 15 seconds for the rest of the day
    // against the same SQLite file the agent writes; `visibilitychange` below catches up the
    // moment it is looked at again, so nothing stale is ever shown.
    //
    // The poll REBUILDS the view only where the event stream cannot: with a subscription running,
    // a tick's revision marker is what says something has changed, and rebuilding on a timer as
    // well would replace the page four times a minute to redraw identical rows. Without one --
    // a browser with no `EventSource`, or a stream that never connected -- the poll is the only
    // thing that would ever refresh the figures, so it does.
    if (document.visibilityState === "visible") void paint(route, !live);
  }, POLL_MS);

  void paint(route, true);
}

/**
 * Navigate, pushing a history entry.
 *
 * @param {Route} route
 */
function go(route) {
  window.history.pushState(null, "", pathFor(route));
  show(route, true);
}

/**
 * Intercept in-app navigation, and only in-app navigation.
 *
 * Every condition below is a case where the browser's own behaviour is the correct one and
 * hijacking it would be a bug a user reports as "your app broke middle-click":
 *
 *   - a modified click (ctrl, meta, shift, alt) or a non-primary button is the user asking for
 *     a new tab, a new window, or a download;
 *   - `target` opens elsewhere, `download` saves rather than navigates;
 *   - a `defaultPrevented` event has already been handled by something closer to the element;
 *   - anything not under `BASE` -- the `<noscript>` link to `/`, and #539's outbound
 *     keeltrading.com links -- is a real navigation and must stay one.
 */
document.addEventListener("click", (event) => {
  if (event.defaultPrevented) return;
  if (event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

  const target = event.target;
  if (!(target instanceof Element)) return;
  const link = target.closest("a");
  if (!link) return;
  if (link.target || link.hasAttribute("download")) return;

  const href = link.getAttribute("href");
  if (!href || !href.startsWith(BASE)) return;

  const route = ROUTES.find((candidate) => pathFor(candidate) === href);
  if (!route) return;

  event.preventDefault();
  if (route === current) return;
  go(route);
});

/**
 * Back and forward. `show` rather than `go`: the history entry already exists, and pushing
 * another would make the back button walk forwards.
 */
window.addEventListener("popstate", () => {
  show(routeFor(window.location.pathname), true);
});

/** Catch up immediately when a hidden tab is looked at again -- see the poll comment in `show`. */
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void paint(current, false);
});

/**
 * Replay a rebuild that was held back while the reader had focus inside the view.
 *
 * `focusout` rather than `blur`, because `blur` does not bubble and the element that lost focus
 * is somewhere inside `#content` rather than on it. The check runs on a zero-delay timeout so it
 * sees where focus LANDED: during `focusout` the incoming element is not focused yet, so
 * `contains(document.activeElement)` would still be true for a Tab from one table cell to the
 * next and the rebuild would be deferred forever.
 */
contentNode.addEventListener("focusout", () => {
  if (!deferred) return;
  window.setTimeout(() => {
    if (deferred && !contentNode.contains(document.activeElement)) void paint(current, true);
  }, 0);
});

// -- live updates (#537) --------------------------------------------------------------------------

/**
 * Whether an `EventSource` subscription is running.
 *
 * Read by the poll above, which falls back to rebuilding on a timer when there is none. Set once,
 * at boot: a browser either has `EventSource` or does not, and a stream that drops is one
 * `live.js` reconnects rather than one this flag turns off -- a dropped connection is reported,
 * never silently downgraded, which is the acceptance criterion.
 *
 * @type {boolean}
 */
const live = available();

subscribe((reading, seen) => {
  // A tick paints the banner only while the view's own read is healthy -- see `reachable`, which
  // exists because two browser runs of #537 found the same class of bug from opposite ends. The
  // rule covers both:
  //
  //   * a healthy fetch and a broken stream -> the tick paints, so a dropped connection is
  //     visible within a heartbeat rather than at the next poll;
  //   * a broken fetch -> the fetch's own sentence stands, whatever the stream says. It is the
  //     more specific one ("the local server at this address did not answer", "that report could
  //     not be built"), and letting both write produced a banner alternating between two
  //     different sentences every few seconds, which reads as an interface that cannot make up
  //     its mind about whether anything is wrong.
  //
  // `engineBanner` is the same function the `fetch` path calls, with the same shape, so a tick
  // and a poll can never word the same state differently. `as_of` moving is how the page proves
  // it is still hearing from keel, and a minted "lost contact" reading is how it says it is not.
  if (reachable) engineBanner(engineNode, reading);

  // `""` is a reading with no tick behind it -- a dropped connection carries no revision, and
  // treating the last one as still current would say "nothing has changed" on the strength of an
  // answer that never arrived.
  if (!seen) return;

  // The first tick establishes the marker without repainting. Rebuilding here would replace a
  // view that was painted a moment ago at boot, for no change at all.
  if (revision && revision !== seen) void paint(current, true);
  revision = seen;
});

// -- boot ---------------------------------------------------------------------------------------

/**
 * Normalise `/static/` and `/static/index.html` to `/static/status` with `replaceState`.
 *
 * `replaceState`, never `pushState`: this happens before the user has done anything, and a
 * history entry here would make the first press of the back button appear to do nothing.
 */
const booted = routeFor(window.location.pathname);
if (window.location.pathname !== pathFor(booted)) {
  window.history.replaceState(null, "", pathFor(booted));
}

/**
 * Register the service worker (#538), keyed to the build that just answered.
 *
 * **After `/api/config`, never before, and that ordering is the whole design.** The worker's
 * cache name comes from the build string, so registering before the build is known would install
 * a worker under a name that has to be corrected on the next load -- two registrations, two
 * caches, for one deployment. Waiting costs one round trip against a local socket.
 *
 * **A failed read registers nothing, deliberately.** With `keel serve` stopped this promise
 * resolves with `data: null`, and the right response is to leave whatever worker is already
 * installed exactly as it is: it is the one serving the shell that is letting the operator read
 * this page at all. Re-registering it under `unknown` would swap a correct cache for an empty
 * one at the precise moment the network cannot refill it.
 *
 * **`encodeURIComponent`, because the build string contains `+`.** `keel.version` produces
 * `0.11.2+88fb17bcab15`, and a raw `+` in a query string decodes to a SPACE -- the worker would
 * read a different build than the one that is running, and the cache key would silently stop
 * tracking the binary it is supposed to track.
 *
 * @param {any} config  `/api/config`'s `data`, or `null`.
 */
function registerWorker(config) {
  if (!("serviceWorker" in navigator)) return;
  const build = (config && (config.build || config.version)) || "";
  if (!build) return;
  // Errors are swallowed on purpose and the app carries on: every failure mode here -- an
  // unsupported browser, a user profile with workers disabled, a private window -- costs the
  // offline shell and nothing else. A dashboard that refused to render because it could not
  // install an optional cache would be trading a working page for a nicety.
  void navigator.serviceWorker
    .register(`${BASE}sw.js?v=${encodeURIComponent(build)}`, { scope: BASE })
    .catch(() => {});
}

/**
 * The build, read once, and the three things that depend on it.
 *
 * Once, not per poll: `/api/config` describes the binary that is answering, and that cannot
 * change without the process restarting -- at which point the session token is new, every fetch
 * is a 403, and the banner says so. A version string re-read four times a minute would be four
 * times a minute spent confirming a constant.
 *
 * **The first view is painted from INSIDE this callback (#539), and that ordering is deliberate.**
 * Every documentation link carries `?v=<build>` (`docs.rememberVersion`), and a link built before
 * the build is known would carry no version until the next poll -- fifteen seconds of links that
 * quietly do not say which build the reader is running, on exactly the first screen they see.
 * `/api/config` is the one endpoint that opens no database, so this costs a single round trip on
 * a loopback socket, and it cannot hang the app: `api.read` resolves with a stopped reading
 * rather than rejecting, so `show` runs even with nothing listening on the port.
 */
void read("config").then((reading) => {
  const config = reading.data;
  rememberVersion((config && (config.build || config.version)) || "");
  docsNode.href = indexUrl();
  buildLine(buildNode, config);
  registerWorker(config);
  show(booted, false);
});
