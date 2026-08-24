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
import { buildLine, engineBanner, placeholderView, statusView, stoppedView } from "./render.js";

/**
 * Where this client is mounted. Becomes `"/"` at #540. See the module note above.
 * @type {string}
 */
const BASE = "/static/";

/**
 * One route.
 * @typedef {object} Route
 * @property {string} name      the path segment under `BASE`, and the nav link's target.
 * @property {string} label     what the nav and `<title>` call it.
 * @property {string} endpoint  the `/api/*` endpoint whose answer drives this view.
 * @property {boolean} built    `false` until #537 builds the view.
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
 * **The `endpoint` of every unbuilt view is `config`, not its own.** A placeholder still needs a
 * truthful engine banner -- "is keel running" is a property of keel, not of the view you happen
 * to be looking at -- and `/api/config` answers that with `needs_database=False` and no database
 * read at all. Pointing an unbuilt view at its real endpoint would build a full insights report
 * in order to render the words "not built yet"; #537 changes one field per view and gets the
 * banner unchanged.
 *
 * @type {Route[]}
 */
const ROUTES = [
  { name: "status", label: "Status", endpoint: "status", built: true },
  { name: "setup", label: "Setup", endpoint: "config", built: false },
  { name: "activity", label: "Activity", endpoint: "config", built: false },
  { name: "insights", label: "Insights", endpoint: "config", built: false },
  { name: "rules", label: "Rules", endpoint: "config", built: false },
  { name: "venues", label: "Venues", endpoint: "config", built: false },
  { name: "gates", label: "Gates", endpoint: "config", built: false },
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
 * This is polling, and it stays polling in this issue. `EventSource` is the spec's answer for
 * live updates and it belongs to #537 along with the views that would benefit from it; adding a
 * server-sent-events endpoint here would be widening the read surface for a view that reloads
 * fine without one.
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
 * Read the current route's endpoint and paint the result.
 *
 * @param {Route} route
 * @param {boolean} rebuild  `true` to replace the view, `false` to refresh the banner only.
 */
async function paint(route, rebuild) {
  const reading = await read(route.endpoint);
  // The user navigated while this was in flight. Drop it: the banner belongs to the route that
  // asked for it, and painting it now would report the wrong endpoint's engine state.
  if (route !== current) return;

  engineBanner(engineNode, reading);

  if (!rebuild) return;

  if (!route.built) {
    contentNode.replaceChildren(placeholderView(route.label));
  } else if (reading.data === null) {
    // `data: null` is the ONLY route into this view, and `payload.envelope` guarantees the key
    // is `null` rather than `{}` for exactly this reason -- see `render.stoppedView`.
    contentNode.replaceChildren(stoppedView(reading));
  } else {
    contentNode.replaceChildren(statusView(reading.data));
  }
  contentNode.setAttribute("aria-busy", "false");
}

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
    if (document.visibilityState === "visible") void paint(route, false);
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
 * The footer's build line, read once.
 *
 * Once, not per poll: `/api/config` describes the binary that is answering, and that cannot
 * change without the process restarting -- at which point the session token is new, every fetch
 * is a 403, and the banner says so. A version string re-read four times a minute would be four
 * times a minute spent confirming a constant.
 */
void read("config").then((reading) => buildLine(buildNode, reading.data));

show(booted, false);
