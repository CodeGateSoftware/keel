// @ts-check
/**
 * Outbound documentation links (#539). **The app fetches, bundles and caches nothing.**
 *
 * ── WHY LINKING IS THE ONLY OPTION, NOT THE CHEAP ONE ───────────────────────────────────────
 * `docs/` lives at the REPOSITORY root, outside `keel/`, and `uv_build` packages the module root
 * -- so no wheel has ever carried it. Every installed deployment, the signed desktop bundle
 * included, renders an empty glossary today, and `keel/commands/help_console.py` says so in its
 * own docstring: "an installed deployment has no docs/ checkout, and the help screen renders
 * that notice as its empty state."
 *
 * That is not fixable by adding a packaging glob -- it is structural. Measured at #535: building
 * with `artifacts` set and with `artifacts = []` produces byte-identical wheels, because
 * `uv_build` ships the whole module root regardless of the key. Linking out is the only option
 * that reaches an installed deployment at all.
 *
 * ── NO OFFLINE FALLBACK, DELIBERATELY ───────────────────────────────────────────────────────
 * No inline definitions, no cached snapshot, no entry in the service worker's `PRECACHE`. An
 * operator running a trading engine has network by definition, and #538's whole argument is that
 * a cached copy of something authoritative is worse than no copy: a definition that has since
 * changed, presented as current, with nothing on screen to say which it is.
 *
 * ── THE ANCHOR CONTRACT, AND WHY A TEST HOLDS IT ────────────────────────────────────────────
 * `docs/glossary.md` states its own rule -- "Each entry is a `## term` heading, a definition,
 * and a `Source:` line" -- and Astro emits kebab-cased IDs for those headings, so the anchor for
 * a term is its heading kebab-cased. Nothing in either repository enforces that from the other
 * side: a heading renamed upstream would break every deep link here **silently**, because a bad
 * fragment is not an error, it is a page that opens at the top.
 *
 * `tests/web/test_doc_links.py` closes that by parsing this table and asserting every anchor
 * exists as a heading in the named document, in this repository, where `docs/` is the source.
 */

/** The published documentation root. One string, spelled once. */
const SITE = "https://keeltrading.com/en/docs/";

/**
 * The running build, for `?v=`.
 *
 * **Version skew is made VISIBLE here, not solved.** The site pins `main` while an operator runs
 * a tagged release, so a linked page can describe behaviour their build does not have. Per-
 * version documentation paths were rejected: `keeltrading.com/en/docs/v0.11.0/glossary` 404s
 * today, and building versioned trees is work in the other repository plus a retention policy,
 * across three languages and a sitemap. Carrying the version in the query string costs nothing
 * and puts the operator's build in the URL bar of the page they are reading.
 *
 * Module state, written exactly once, at boot, by `main.js` -- the alternative is threading a
 * version string through every render function to reach the four places that build a link.
 */
let version = "";

/**
 * Record the build every documentation link should carry. Called once from `main.js`, from the
 * same `/api/config` read that fills the footer.
 *
 * @param {string} build
 */
export function rememberVersion(build) {
  version = typeof build === "string" ? build : "";
}

/**
 * The URL for one document, optionally at one anchor.
 *
 * The trailing slash on the slug is not cosmetic: the site serves `…/docs/glossary/index.html`,
 * and the un-slashed form is a redirect that some browsers resolve by dropping the fragment --
 * a deep link that lands at the top of the page, which is exactly the failure this module's
 * anchor table exists to prevent.
 *
 * @param {string} slug    a document slug, or `""` for the documentation index.
 * @param {string} anchor  a heading anchor, or `""` for the top of the page.
 * @returns {string}
 */
export function documentUrl(slug, anchor) {
  let url = SITE;
  if (slug) url = url + slug + "/";
  if (version) url = url + "?v=" + encodeURIComponent(version);
  if (anchor) url = url + "#" + anchor;
  return url;
}

/** The documentation index, for the header's outbound link. @returns {string} */
export function indexUrl() {
  return documentUrl("", "");
}

/**
 * The labels this client puts on screen that name a term the documentation defines, mapped to
 * where it is defined.
 *
 * **Keyed by the LABEL, not by the term.** The alternative -- tagging each call site with a term
 * name -- spreads the decision across six views and makes "which words on this screen are
 * defined somewhere" unanswerable without reading all of them. Keyed by label, the whole answer
 * is this table, and `kv` consults it for every pair it builds, so a label that names a term is
 * a link wherever it appears without a call site knowing.
 *
 * **Deliberately NOT exhaustive, and the omissions are the point.** `mode` reads `paper` or
 * `live` and would need two different targets for one label; `evidence required` sits on the
 * CAPABILITY gates (`keel.capabilities.GATES`), not the promotion gate, and linking it to
 * `#promotion-gate` would be confidently wrong. A missing link costs a reader one search. A
 * wrong one costs them their trust in every other link on the page.
 *
 * @type {Record<string, {slug: string, anchor: string}>}
 */
export const TERMS = {
  autonomy: { slug: "glossary", anchor: "autonomy" },
  "autonomy configured": { slug: "glossary", anchor: "autonomy" },
  "autonomy lapses": { slug: "glossary", anchor: "autonomy" },
  "kill switch": { slug: "glossary", anchor: "kill-switch" },
  "rail 11": { slug: "glossary", anchor: "rail" },
  "withdrawal attestation (rail 17)": { slug: "glossary", anchor: "attestation" },
  "market session": { slug: "glossary", anchor: "market-clock" },
  session: { slug: "glossary", anchor: "session-bound-venue" },
  "paper stage": { slug: "glossary", anchor: "paper-mode" },
};

/**
 * A label as an outbound link to its definition, or `null` if the label names no term.
 *
 * `rel="noopener"` with `target="_blank"`: a new tab opened without it gets a `window.opener`
 * handle back to this page, and this page is a trading console on a token-bearing origin.
 * `noreferrer` too -- the server already sends `Referrer-Policy: no-referrer`, and a link that
 * states it as well is one that keeps holding if this markup is ever read somewhere the header
 * is not sent.
 *
 * @param {string} label
 * @returns {HTMLAnchorElement | null}
 */
export function termLink(label) {
  const target = Object.prototype.hasOwnProperty.call(TERMS, label) ? TERMS[label] : null;
  if (!target) return null;
  const anchor = document.createElement("a");
  anchor.className = "doclink";
  anchor.textContent = label;
  anchor.href = documentUrl(target.slug, target.anchor);
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  // Named for a reader who arrives on the link out of context, and hears only the link text.
  anchor.title = "Definition on keeltrading.com — opens in a new tab";
  return anchor;
}
