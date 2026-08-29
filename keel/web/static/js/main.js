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
 * ── THE MOUNT IS `/` ─────────────────────────────────────────────────────────────────────────
 *
 * Since #540, which deleted the seven server-rendered pages that owned the root paths. `BASE` is
 * the one place in the JavaScript that says so; `index.html`'s hrefs and
 * `staticfiles.STATIC_PREFIX` are the other two places it is spelled, and
 * `tests/web/test_client_assets.py::test_the_mount_prefix_is_spelled_the_same_everywhere` pins
 * that the three agree -- which is what made that move an edit rather than a hunt.
 *
 * A `<base href>` would have collapsed those three into one. It is not available: `_STATIC_CSP`
 * sets `base-uri 'none'`, and an injected `<base>` retargeting every relative URL on the page is
 * precisely what that directive exists to stop. Absolute hrefs are the cost of that defence, and
 * it is worth paying.
 */

import { perform, recorded, remember } from "./actions.js";
import { read } from "./api.js";
import { highlightTrade, showTradeAt } from "./chart.js";
import { indexUrl, rememberVersion } from "./docs.js";
import { available, subscribe } from "./live.js";
import {
  activityView,
  buildLine,
  engineBanner,
  gatesView,
  insightsView,
  modeBadge,
  rulesView,
  setupView,
  statusView,
  stoppedView,
  venuesView,
} from "./render.js";

/**
 * Where this client is mounted. `/` since #540. See the module note above.
 * @type {string}
 */
const BASE = "/";

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
/** Where the "a newer build is ready" offer goes (#538). Empty until there is one. */
const updateNode = must("update");
/** The header's mode badge (#597). Filled once, from the boot config read. */
const modeNode = must("mode-badge");
/** The header's theme toggle (#597). Clicked, it flips and stores the choice `theme.js` restores. */
const themeNode = /** @type {HTMLButtonElement} */ (must("theme-toggle"));

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

  // #602: the chart `rebuildInto` just replaced is a fresh `<svg>` at `chart.js`'s default
  // `viewBox` -- the poll and every live tick run through here, so without this a reader's zoom
  // would reset itself every fifteen seconds. Same shape as the `<details>` state and the
  // action-outcome restore below: state a rebuild would otherwise erase, re-applied in the one
  // function every path that replaces the view runs through.
  reapplyChartView();

  // What each action last reported, re-applied here rather than at the one call site that
  // triggered a rebuild. **The first version restored only after an action's own repaint, and the
  // message then survived exactly until the next 15-second poll wiped it** -- long enough to look
  // like it worked, short enough that an operator reading the outcome watched it vanish
  // mid-sentence. Every path that replaces the view comes through this function, which is why it
  // is the one place this can be correct. Same argument as the `<details>` state above.
  restoreOutcomes();

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

  // #602: reset on every mount, not only the insights one -- leaving the chart's view interaction
  // armed on a view with no chart would have its wheel/pointer handlers doing arithmetic against
  // a curve from whichever route was showing last.
  activeCurve = null;

  if (route.name === "setup") {
    // The write token for this session, kept for the submit handler below. It is read off the
    // document that was just fetched rather than stored anywhere: it dies with the process that
    // minted it, and a stale one produces a 403 the view shows rather than a silent no-op.
    remember(data);
    return setupView(data);
  }
  if (route.name === "activity") {
    return activityView(data, primary.sort, onSort, (scope) => {
      paramsFor(route.endpoints[0]).scope = scope;
      void paint(route, true, true);
    });
  }
  if (route.name === "insights") {
    const journal = readings[1];
    // Kept outside the DOM for the zoom/pan/cursor math below, which reads `curve.points` and
    // `curve.width`/`height` -- bare positions, never a `Field`'s `.value` -- to do the
    // arithmetic `chart.js` is not allowed to (see that module's note on why).
    activeCurve = journal && journal.data ? journal.data.curve : null;
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

// -- the theme choice (#597) ----------------------------------------------------------------------

/**
 * The light/dark toggle: flip the theme, store the choice, and let the stylesheet notice.
 *
 * ── TWO STATES, AND THE ONE ATTRIBUTE THAT CARRIES THE CHOICE ────────────────────────────────
 * `data-theme` on `<html>` is the whole mechanism, and `keel.css` reads it from two selectors:
 * the dark `@media (prefers-color-scheme: dark)` block applies when the OS prefers dark and the
 * reader has NOT pinned light (`:root:not([data-theme="light"])`), and a `:root[data-theme=
 * "dark"]` block applies a pinned dark on any OS. So "no stored choice" needs no attribute at
 * all -- the OS decides -- and a stored choice is one of the two words.
 *
 * ── WHY `current` IS COMPUTED RATHER THAN READ OFF THE ATTRIBUTE ─────────────────────────────
 * When the reader has not chosen, the attribute is absent but the PAGE still has a theme: the
 * one the OS preference painted. Flipping "absent" to `"dark"` on a dark-OS machine would be a
 * no-op button on exactly the machine where the choice is most likely to be made; so the flip
 * starts from the theme that is actually showing, asking `matchMedia` when the attribute does
 * not answer. This is keeltrading.com's own toggle logic (`Header.astro`'s delegated listener),
 * carried across rather than reinvented -- one identity, one behaviour.
 *
 * ── THE STORE, AND WHY THE WRITE IS GUARDED ──────────────────────────────────────────────────
 * `keel-theme` in `localStorage`, the key `js/theme.js` restores before first paint. A reader
 * whose storage is refused (private mode, disabled by policy) still gets a working toggle for
 * THIS load -- the attribute flips, the page re-paints -- and simply starts from the OS
 * preference next time, which is the same place a first-time reader starts. The two spellings
 * of the key are pinned to agree by `test_the_theme_choice_is_spelled_where_it_is_stored`.
 */
themeNode.addEventListener("click", () => {
  const root = document.documentElement;
  const pinned = root.dataset.theme;
  const showing =
    pinned === "light" || pinned === "dark"
      ? pinned
      : window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
  const next = showing === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try {
    window.localStorage.setItem("keel-theme", next);
  } catch (cause) {
    void cause; // storage refused: this load still flips; the next one starts from the OS again
  }
});

/** Catch up immediately when a hidden tab is looked at again -- see the poll comment in `show`. */
/**
 * The write path (#540): one delegated `submit` listener for every action form.
 *
 * Delegated on `contentNode` rather than bound per form, because the view is rebuilt from
 * scratch on every poll and on every tick -- a listener attached to a form would be attached to
 * a node that is about to be replaced, and re-binding after each rebuild is the bug that shows
 * up as "the button works until the page refreshes".
 *
 * **The form is disabled for the duration and re-enabled by the rebuild.** A setup action is
 * idempotent, so a double submission is harmless by construction; this is about the operator
 * being able to tell that something is happening, not about protecting the server.
 */
contentNode.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  const key = form.getAttribute("data-action");
  if (!key) return;
  // Always: this page never performs a native form submission. The `<form>` element is used for
  // its semantics and its keyboard behaviour (Enter submits, labels bind to controls), and the
  // navigation it would otherwise cause is exactly what a client-rendered view must not do.
  event.preventDefault();

  const values = {};
  for (const control of form.elements) {
    const name = control.getAttribute && control.getAttribute("name");
    if (name) values[name] = control.value;
  }

  setBusy(form, true);
  showOutcome(key, "Running…");

  // The token, the headers, the idempotency semantics and the sentence all live in `actions.js`.
  // What is left here is what is genuinely this file's job: read the form, show the answer,
  // repaint. See that module's note on whose shape it is.
  void perform(key, values).then((outcome) => {
    showOutcome(key, outcome);
    // Re-read the whole view: a step that ran has changed the deployment this page describes, and
    // patching one card would leave the checklist above it saying the opposite. The outcome
    // survives that rebuild because `rebuildInto` re-applies every recorded one.
    void paint(routeFor(window.location.pathname), true, false);
  });
});

/**
 * Put an action's outcome into its card, if that card is on screen.
 *
 * @param {string} key
 * @param {string} text
 */
function showOutcome(key, text) {
  const form = contentNode.querySelector('form[data-action="'.concat(key, '"]'));
  const node = form ? form.querySelector(".action-outcome") : null;
  if (!node) return;
  node.replaceChildren(document.createTextNode(text));
}

/**
 * Re-apply every remembered outcome after a rebuild has replaced the cards.
 *
 * Strings rather than nodes, since the memory moved into `actions.js`. That is not only tidier:
 * a `Node` can be in one place in a document at a time, so a map of nodes had to be cloned on
 * every restore or the second rebuild would move the only copy out of the map's reach. A string
 * has no such property, and the bug it invites cannot be written.
 */
function restoreOutcomes() {
  for (const [key, text] of recorded()) showOutcome(key, text);
}

/**
 * Disable or re-enable every control in a form.
 *
 * @param {HTMLFormElement} form
 * @param {boolean} busy
 */
function setBusy(form, busy) {
  for (const control of form.elements) {
    if (control instanceof HTMLElement) control.toggleAttribute("disabled", busy);
  }
}

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

// -- the equity chart (#602) ----------------------------------------------------------------------

/**
 * `/api/journal`'s `data.curve`, kept outside the DOM for as long as the insights view is on
 * screen, or `null` everywhere else. `insightsView`'s own `<svg>` carries `curve.points` nowhere
 * a DOM query could recover them -- `chart.js` draws positions, it does not label its shapes with
 * the data behind them -- so the nearest-point search, the zoom math and the pan math all need
 * their own copy of the curve they are reading. Never read for a `Field`'s `.value`: every use
 * below reads `width`, `height`, `x` or `index`, which are bare positions, or `pnl`/`at`/etc. to
 * HAND to `chart.js`, never to judge here.
 *
 * @type {any}
 */
let activeCurve = null;

/**
 * The reader's own zoom/pan, as the four-number string `setAttribute("viewBox", ...)` wants, or
 * `null` before anyone has touched it. See `reapplyChartView`'s own note on why this lives here
 * rather than on the element it describes.
 *
 * @type {string|null}
 */
let chartViewBox = null;

/**
 * A drag-to-pan in progress, or `null`. `box` is the `viewBox` the drag STARTED from, read once
 * at `pointerdown` -- computing the new position from the live `viewBox` on every `pointermove`
 * would compound the rounding of each previous frame into the next; computing it from one fixed
 * start and the total distance dragged so far does not.
 *
 * @type {{pointerId: number, clientX: number, x: number, y: number, width: number, height: number}|null}
 */
let panning = null;

/** How much one wheel notch zooms. */
const CHART_ZOOM_STEP = 1.2;

/** The narrowest slice of the full width a reader can zoom into -- a twenty-fifth of it, so the
 * view cannot be zoomed down to nothing and lose the curve entirely. */
const CHART_MIN_SPAN_FRACTION = 0.04;

/** How much one arrow-key press pans, as a fraction of the CURRENT view's width -- so panning
 * stays a usefully sized step whether zoomed in close or looking at the full curve. */
const CHART_KEYBOARD_PAN_FRACTION = 0.2;

/**
 * The `<svg class="curve">` a pointer or wheel event landed on, or `null` off the chart entirely.
 *
 * Every chart listener below is delegated on `contentNode` rather than attached to the `<svg>`
 * itself, for the reason `main.js`'s other delegated listeners already are: `rebuildInto` replaces
 * the whole view, `<svg>` included, on every poll and every tick, and a listener bound to a
 * specific element is a listener bound to a node about to be discarded.
 *
 * @param {EventTarget|null} target
 * @returns {SVGSVGElement|null}
 */
function chartSvgFrom(target) {
  if (!(target instanceof Element)) return null;
  const found = target.closest("svg.curve");
  return found instanceof SVGSVGElement ? found : null;
}

/**
 * The `tr[data-point-index]` a pointer or focus event landed on or inside, or `null`.
 *
 * @param {EventTarget|null} target
 * @returns {HTMLElement|null}
 */
function journalRowFrom(target) {
  if (!(target instanceof Element)) return null;
  const found = target.closest("tr[data-point-index]");
  return found instanceof HTMLElement ? found : null;
}

/**
 * Where in the curve's own coordinate space `clientX` sits, given `canvas`'s CURRENT `viewBox`.
 *
 * @param {SVGSVGElement} canvas
 * @param {number} clientX
 * @returns {number}
 */
function dataXAt(canvas, clientX) {
  const rect = canvas.getBoundingClientRect();
  const box = canvas.viewBox.baseVal;
  const fraction = rect.width === 0 ? 0 : (clientX - rect.left) / rect.width;
  return box.x + fraction * box.width;
}

/**
 * The point in `points` whose `x` is closest to `dataX`.
 *
 * A linear scan, not a binary search: `_plot_x` spaces points evenly by TRADE ORDER, not by `x`
 * value directly, and trusting that spacing here would be this file assuming a layout decision
 * `keel.commands.insights.build_equity_curve` owns. A journal is, realistically, hundreds of rows;
 * a scan over it on a pointer move that browsers already coalesce is not a cost worth a second
 * copy of that Python module's own arithmetic.
 *
 * @param {any[]} points
 * @param {number} dataX
 * @returns {any}
 */
function nearestPoint(points, dataX) {
  let best = points[0];
  let bestDistance = Infinity;
  for (const point of points) {
    const distance = Math.abs(Number(point.x) - dataX);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = point;
    }
  }
  return best;
}

/**
 * Set `canvas`'s `viewBox` and remember it, so `reapplyChartView` can restore it past the next
 * rebuild.
 *
 * @param {SVGSVGElement} canvas
 * @param {number} x
 * @param {number} y
 * @param {number} width
 * @param {number} height
 */
function applyViewBox(canvas, x, y, width, height) {
  const box = [x, y, width, height].join(" ");
  canvas.setAttribute("viewBox", box);
  chartViewBox = box;
}

/**
 * Zoom `canvas` by one wheel notch, centred on `clientX` -- the point under the pointer stays
 * under it as the view narrows or widens, which is what makes repeated small notches feel like
 * zooming toward something rather than toward the box's corner.
 *
 * @param {SVGSVGElement} canvas
 * @param {number} clientX
 * @param {number} deltaY
 */
function zoomBy(canvas, clientX, deltaY) {
  if (!activeCurve) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0) return;
  const box = canvas.viewBox.baseVal;
  const fullWidth = Number(activeCurve.width);
  const fullHeight = Number(activeCurve.height);
  const fraction = (clientX - rect.left) / rect.width;
  const dataX = box.x + fraction * box.width;

  const factor = deltaY > 0 ? CHART_ZOOM_STEP : 1 / CHART_ZOOM_STEP;
  const minWidth = fullWidth * CHART_MIN_SPAN_FRACTION;
  const width = Math.min(fullWidth, Math.max(minWidth, box.width * factor));
  const unclampedX = dataX - fraction * width;
  const x = Math.min(Math.max(unclampedX, 0), fullWidth - width);

  applyViewBox(canvas, x, 0, width, fullHeight);
}

/**
 * Pan `canvas` to wherever dragging to `clientX` puts it, measured from where `panning` started.
 *
 * @param {SVGSVGElement} canvas
 * @param {number} clientX
 */
function panTo(canvas, clientX) {
  if (!panning || !activeCurve) return;
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0) return;
  const fullWidth = Number(activeCurve.width);
  const deltaPx = clientX - panning.clientX;
  const deltaData = (deltaPx / rect.width) * panning.width;
  const unclampedX = panning.x - deltaData;
  const x = Math.min(Math.max(unclampedX, 0), fullWidth - panning.width);

  applyViewBox(canvas, x, panning.y, panning.width, panning.height);
}

/**
 * Re-apply the reader's own zoom/pan after a rebuild has drawn a fresh `<svg>` at `chart.js`'s
 * default `viewBox`.
 *
 * Called from `rebuildInto`, the one function every path that replaces the view runs through --
 * see that function's own note on why state that must survive a rebuild is restored there and
 * nowhere else. A no-op before the reader has zoomed at all, and a no-op on any view with no
 * chart in it.
 */
function reapplyChartView() {
  if (!chartViewBox) return;
  const canvas = contentNode.querySelector("svg.curve");
  if (canvas instanceof SVGSVGElement) canvas.setAttribute("viewBox", chartViewBox);
}

/**
 * Highlight the trade `row` names, or clear the highlight when `row` is `null`.
 *
 * @param {HTMLElement|null} row
 */
function highlightJournalRow(row) {
  const figure = contentNode.querySelector("figure.chart");
  if (!(figure instanceof HTMLElement)) return;

  if (!row || !activeCurve) {
    highlightTrade(figure, null, null);
    return;
  }
  const index = Number(row.dataset.pointIndex);
  const point = activeCurve.points[index];
  if (!point) {
    highlightTrade(figure, null, null);
    return;
  }
  const previous = index > 0 ? activeCurve.points[index - 1] : null;
  highlightTrade(figure, previous, point);
}

/** Zooming and panning, delegated on `contentNode` -- see `chartSvgFrom`'s own note on why. */
contentNode.addEventListener(
  "wheel",
  (event) => {
    const canvas = chartSvgFrom(event.target);
    if (!canvas) return;
    // The chart, not the page, owns this scroll gesture while the pointer is over it.
    event.preventDefault();
    zoomBy(canvas, event.clientX, event.deltaY);
  },
  // Default-prevented, so this listener must not be passive.
  { passive: false },
);

contentNode.addEventListener("pointerdown", (event) => {
  const canvas = chartSvgFrom(event.target);
  if (!canvas || !activeCurve || event.button !== 0) return;
  const box = canvas.viewBox.baseVal;
  panning = {
    pointerId: event.pointerId,
    clientX: event.clientX,
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
  };
  canvas.setPointerCapture(event.pointerId);
});

contentNode.addEventListener("pointerup", (event) => {
  if (panning && panning.pointerId === event.pointerId) panning = null;
});
contentNode.addEventListener("pointercancel", (event) => {
  if (panning && panning.pointerId === event.pointerId) panning = null;
});

/**
 * Arrow keys pan, `+`/`-` zoom, `0` resets -- the keyboard path to the wheel/drag gestures above,
 * for a reader who has Tabbed to the chart (`chart.js` gives `svg.curve` `tabindex="0"` for
 * exactly this). Centred on the MIDDLE of the current view rather than on a pointer position,
 * since a key press has no `clientX` of its own.
 */
contentNode.addEventListener("keydown", (event) => {
  const canvas = chartSvgFrom(event.target);
  if (!canvas || !activeCurve) return;
  const box = canvas.viewBox.baseVal;
  const fullWidth = Number(activeCurve.width);
  const fullHeight = Number(activeCurve.height);

  if (event.key === "0" || event.key === "Home") {
    event.preventDefault();
    applyViewBox(canvas, 0, 0, fullWidth, fullHeight);
    return;
  }

  const panStep = box.width * CHART_KEYBOARD_PAN_FRACTION;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const x = Math.min(Math.max(box.x + direction * panStep, 0), fullWidth - box.width);
    applyViewBox(canvas, x, box.y, box.width, box.height);
    return;
  }

  if (event.key === "+" || event.key === "-" || event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    const zoomingIn = event.key === "+" || event.key === "ArrowUp";
    const factor = zoomingIn ? 1 / CHART_ZOOM_STEP : CHART_ZOOM_STEP;
    const middle = box.x + box.width / 2;
    const minWidth = fullWidth * CHART_MIN_SPAN_FRACTION;
    const width = Math.min(fullWidth, Math.max(minWidth, box.width * factor));
    const x = Math.min(Math.max(middle - width / 2, 0), fullWidth - width);
    applyViewBox(canvas, x, 0, width, fullHeight);
  }
});

/** The cursor legend, and panning while a drag is in progress -- one event, two jobs, chosen by
 * whether `panning` is set, the same way `show`'s poll and `subscribe`'s tick already share one
 * rebuild path chosen by `reachable`. */
contentNode.addEventListener("pointermove", (event) => {
  const canvas = chartSvgFrom(event.target);
  if (!canvas) return;

  if (panning && panning.pointerId === event.pointerId) {
    panTo(canvas, event.clientX);
    return;
  }
  if (!activeCurve) return;
  const figure = canvas.closest("figure.chart");
  if (!(figure instanceof HTMLElement)) return;
  const point = nearestPoint(activeCurve.points, dataXAt(canvas, event.clientX));
  showTradeAt(figure, point);
});

/** `pointerleave` does not bubble, so hiding the legend on "the pointer left the chart" has to go
 * through `pointerout` plus a check of where it went -- the same shape `rebuildInto`'s own
 * `focusout` listener uses for "did focus actually leave". */
contentNode.addEventListener("pointerout", (event) => {
  const canvas = chartSvgFrom(event.target);
  if (!canvas) return;
  const figure = canvas.closest("figure.chart");
  if (!(figure instanceof HTMLElement)) return;
  const related = event.relatedTarget;
  if (related instanceof Node && figure.contains(related)) return;
  showTradeAt(figure, null);
});

/** Reset view and save-as-image, delegated the same way the sort headers' OWN clicks are not --
 * those are bound directly in `render.js` because they need no arithmetic; these do (a `viewBox`
 * reset, a canvas resolution), so they are wired here instead. See `chart.js`'s module note. */
contentNode.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const button = target.closest("[data-chart-action]");
  if (!button || !activeCurve) return;

  const canvas = contentNode.querySelector("svg.curve");
  const figure = contentNode.querySelector("figure.chart");
  if (!(canvas instanceof SVGSVGElement) || !(figure instanceof HTMLElement)) return;

  const action = button.getAttribute("data-chart-action");
  if (action === "reset-view") {
    applyViewBox(canvas, 0, 0, Number(activeCurve.width), Number(activeCurve.height));
  } else if (action === "save-image") {
    void saveChartImage(figure, canvas);
  }
});

/** A trade row's hover or keyboard focus highlights its trade on the chart above -- see
 * `keel.css`'s `tbody tr[data-point-index]` note on why focus, not only hover, reaches a row. */
contentNode.addEventListener("pointerover", (event) => {
  highlightJournalRow(journalRowFrom(event.target));
});
contentNode.addEventListener("pointerout", (event) => {
  const row = journalRowFrom(event.target);
  if (!row) return;
  const related = event.relatedTarget;
  if (related instanceof Node && row.contains(related)) return;
  highlightJournalRow(null);
});
contentNode.addEventListener("focusin", (event) => {
  highlightJournalRow(journalRowFrom(event.target));
});
contentNode.addEventListener("focusout", (event) => {
  if (journalRowFrom(event.target)) highlightJournalRow(null);
});

/**
 * The CSS properties copied from the live chart onto a clone of it before `saveChartImage`
 * serialises that clone. A clone detached from the document has no `<link>` to `keel.css` and
 * so no colour, no stroke width and no `display: none` on whatever is currently hidden -- it
 * would rasterise as black lines on a transparent box with every overlay showing at once.
 *
 * Generic rather than one list per element class, so a later edit to this file's CSS (a new
 * overlay, a retuned stroke width) is picked up here with no second list to remember to update.
 *
 * @type {string[]}
 */
const CHART_EXPORT_PROPERTIES = [
  "display",
  "fill",
  "stroke",
  "stroke-width",
  "stroke-dasharray",
  "stroke-linecap",
  "stroke-linejoin",
  "opacity",
  "color",
];

/**
 * Copy `CHART_EXPORT_PROPERTIES`'s computed values from every node in `live` onto the
 * correspondingly-positioned node in `clone`, as an inline `style` attribute.
 *
 * Matched by POSITION in `querySelectorAll("*")`'s document order, which `cloneNode(true)`
 * guarantees `clone` walks identically to `live` -- a clone is the same tree with no listeners
 * and no attachment to the document, never a re-ordering of it.
 *
 * @param {SVGSVGElement} live
 * @param {SVGSVGElement} clone
 */
function inlineComputedStyle(live, clone) {
  const liveNodes = [live, ...live.querySelectorAll("*")];
  const cloneNodes = [clone, ...clone.querySelectorAll("*")];
  for (const [index, liveNode] of liveNodes.entries()) {
    const cloneNode = cloneNodes[index];
    if (!(liveNode instanceof Element) || !(cloneNode instanceof Element)) continue;
    const computed = window.getComputedStyle(liveNode);
    const declarations = [];
    for (const property of CHART_EXPORT_PROPERTIES) {
      const value = computed.getPropertyValue(property);
      if (value) declarations.push([property, value].join(":"));
    }
    cloneNode.setAttribute("style", declarations.join(";"));
  }
}

/**
 * Trigger a save of `blob` as `filename`, with no server and no navigation.
 *
 * A `blob:` URL, never a `data:` one: the artifact this whole feature exists to avoid is a
 * second CSP question (`img-src`/`default-src` do not cover an already-in-memory object the way
 * they cover a URL scheme), and a `blob:` reference to bytes this page already holds never raises
 * one. The link is never attached to anything a reader sees -- it exists for one synthetic click.
 *
 * @param {Blob} blob
 * @param {string} filename
 */
function downloadChartBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * Save the chart as a PNG, drawn locally (#602).
 *
 * **No `data:` URL, anywhere in this path.** `_STATIC_CSP` is `default-src 'self'` with no
 * `img-src` override, so `img-src` falls back to it and an `<img>` or `Image()` pointed at a
 * `data:` URL would be a CSP violation, refused rather than drawn. `createImageBitmap(blob)`
 * decodes bytes already sitting in this page's own memory -- there is no URL for any `-src`
 * directive to apply to, and nothing crosses the network either way.
 *
 * The cursor legend is deliberately absent from the exported image: it is an HTML overlay
 * outside the `<svg>` (`chart.js`'s module note explains why its text cannot live inside the
 * viewBox-scaled drawing), and an export is a record of the curve, not of wherever the pointer
 * happened to be when the button was pressed. A highlighted trade, if one is showing, IS inside
 * the `<svg>` and is exported with it.
 *
 * @param {HTMLElement} figure
 * @param {SVGSVGElement} canvas
 */
async function saveChartImage(figure, canvas) {
  const rect = canvas.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return;
  // 2x the on-screen size: a PNG has no intrinsic resolution of its own the way the SVG does, so
  // this is the one place "how sharp" has to be decided, and twice the CSS pixel size is what a
  // reader's own device pixel ratio already assumes for a "retina" image.
  const scale = 2;
  const width = rect.width * scale;
  const height = rect.height * scale;

  const clone = canvas.cloneNode(true);
  if (!(clone instanceof SVGSVGElement)) return;
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  inlineComputedStyle(canvas, clone);

  const markup = new XMLSerializer().serializeToString(clone);
  const svgBlob = new Blob([markup], { type: "image/svg+xml" });

  let bitmap;
  try {
    bitmap = await createImageBitmap(svgBlob);
  } catch (cause) {
    void cause; // an unsupported browser loses the export; the chart on screen is unaffected
    return;
  }

  const target = document.createElement("canvas");
  target.width = width;
  target.height = height;
  const context = target.getContext("2d");
  if (!context) return;
  // The card's own background, read off the live figure: the `<svg>` itself has none (the page
  // paints it via `.chart { background: var(--card) }`), and a transparent PNG would paste onto
  // whatever the reader pastes it on next, in whichever colour that happens to be.
  context.fillStyle = window.getComputedStyle(figure).backgroundColor;
  context.fillRect(0, 0, width, height);
  context.drawImage(bitmap, 0, 0, width, height);

  target.toBlob((pngBlob) => {
    if (pngBlob) downloadChartBlob(pngBlob, "keel-equity-curve.png");
  }, "image/png");
}

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
 * Normalise `/` and `/index.html` to `/status` with `replaceState`.
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
    .then((registration) => watchForUpdate(registration))
    .catch(() => {});
}

/**
 * Offer the operator a new build once one has finished installing (#538, corrected).
 *
 * **Why there is a prompt at all.** The worker used to call `skipWaiting()` the moment its cache
 * was full, which takes over the page currently on screen -- a document rendered by the OLD
 * build's JavaScript, whose later requests are then answered from the NEW build's cache. `sw.js`
 * carries the full argument. The fix is that the new worker waits, and this is what tells the
 * operator it is waiting.
 *
 * **Three states, because a worker can already be waiting when this runs.** A registration whose
 * `waiting` is populated has an update that installed during a previous visit; `updatefound` plus
 * `installed` catches one that arrives while the page is open. Both funnel to the same offer.
 *
 * `navigator.serviceWorker.controller` is the test for "is this an UPDATE or a first install".
 * Without it, the very first visit -- where a worker installs and waits with nothing to replace
 * -- would offer the operator a reload for a build they are already running.
 *
 * **It gates the OFFER, not the watching, and the first spelling got that wrong.** Returning
 * early when there is no controller meant that on a first visit -- the one load where there
 * reliably is none -- the `updatefound` listener was never attached at all, so an update arriving
 * later in that same session went unnoticed. The window was narrow (an update needs a server
 * restart, which invalidates the session token, which the banner reports) but the code was saying
 * something it did not mean. Found by driving the flow in a browser rather than by reading it.
 *
 * @param {ServiceWorkerRegistration} registration
 */
function watchForUpdate(registration) {
  if (registration.waiting && navigator.serviceWorker.controller) {
    offerUpdate(registration.waiting);
  }
  registration.addEventListener("updatefound", () => {
    const installing = registration.installing;
    if (!installing) return;
    installing.addEventListener("statechange", () => {
      if (installing.state === "installed" && navigator.serviceWorker.controller) {
        offerUpdate(installing);
      }
    });
  });
}

/**
 * The offer itself: one line in the footer, and a button that takes it.
 *
 * In the FOOTER rather than over the view, and not in the engine banner. The banner is the page's
 * one `aria-live` region and it answers "is keel running"; an upgrade notice is neither urgent nor
 * about the engine's state, and putting it there would interrupt a screen reader mid-table to say
 * something that can wait indefinitely. It sits beside the build line it is about to change.
 *
 * The reload is driven by `controllerchange` rather than fired straight after the message: the
 * new worker has to actually take over before a reload gets the new build, and reloading first
 * would fetch the old one again and leave the offer standing.
 *
 * @param {ServiceWorker} waiting
 */
function offerUpdate(waiting) {
  if (updateNode.childElementCount !== 0) return;

  const button = document.createElement("button");
  button.className = "update";
  button.setAttribute("type", "button");
  button.append(document.createTextNode("A newer build is ready — reload"));
  button.addEventListener("click", () => {
    button.disabled = true;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      window.location.reload();
    });
    waiting.postMessage("SKIP_WAITING");
  });
  updateNode.replaceChildren(button);
}

/**
 * The build, read once, and the four things that depend on it.
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
 *
 * **The header's mode badge fills here too (#597), for the same reason the links do.** The
 * deployment this process serves cannot change without a restart, so the badge would never
 * need re-reading -- and hydrating it from the read every view already makes is what puts it
 * on EVERY view, rather than only where a status report happens to load.
 */
void read("config").then((reading) => {
  const config = reading.data;
  rememberVersion((config && (config.build || config.version)) || "");
  docsNode.href = indexUrl();
  buildLine(buildNode, config);
  modeBadge(modeNode, config);
  registerWorker(config);
  show(booted, false);
});
