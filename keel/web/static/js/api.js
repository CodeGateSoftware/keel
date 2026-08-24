// @ts-check

/**
 * The single `fetch` wrapper. **The only place `fetch` appears in this client.**
 *
 * That is a checkable property, not an aspiration:
 * `tests/web/test_client_assets.py::test_fetch_appears_in_exactly_one_client_module` greps the
 * shipped `.js` files for it. It matters because the one guarantee the design spec sells hardest
 * -- "the interface is provably incapable of sending positions, equity or trade history anywhere
 * but the local process" -- is enforced by `connect-src 'self'` in the browser, and audited by a
 * reader who wants to see for themselves. A reader can audit one file. Nine is a search.
 *
 * ── WHAT THIS MODULE IS ACTUALLY FOR: MAKING FOUR OUTCOMES INTO ONE SHAPE ────────────────────
 *
 * A `GET /api/*` can end four ways, and `keel/web/api.py` deliberately does NOT make them look
 * alike on the wire:
 *
 *   1. **200 with data.** `{as_of, engine, data, sort}`.
 *   2. **200 with `data: null`.** No deployment on this machine. `engine.value` is `"stopped"`
 *      and the HTTP status is 200 ON PURPOSE -- `api.respond`'s docstring spells out why: "#536's
 *      `fetch` wrapper reads a non-ok status as 'the server is unreachable', which would put a
 *      first-run user into an outage state when their actual position is that they have not set
 *      anything up yet." This module is the one that must not make that mistake, so: a 200 is
 *      never an error here, whatever `data` holds.
 *   3. **A 4xx/5xx error document.** `{as_of, data: null, error}` -- and note it carries NO
 *      `engine` key. The server's reason: most refusals happen before the session cookie is
 *      checked, and filling in `engine` there would mean an unauthenticated request reading the
 *      deployment state off disk.
 *   4. **No response at all.** `keel serve` was stopped, or the machine went to sleep. `fetch`
 *      rejects; there is no document of any kind.
 *
 * Every caller of this module gets ONE shape back, `Reading`, with the same four keys always
 * present -- which is the same discipline `payload.envelope` states for itself ("The key set is
 * CONSTANT across every endpoint and every state ... A client that has to test whether a key is
 * present is branching on payload SHAPE"). Applying a rule to the client that the server applies
 * to itself is the point: `render.js` reads `.engine`, `.data` and `.error` unconditionally and
 * has no idea which of the four cases produced them.
 *
 * ── THE THREE `state` VALUES THIS MODULE MINTS, AND WHY THEY ARE NOT ALL `warn` ───────────────
 *
 * `payload.engine_state` judges a stopped engine `warn`, and says why: "on the commonest path to
 * this value nothing is broken at all -- it is a first run." Cases 3 and 4 are not that path.
 * A server that answered with a refusal, and a server that has stopped answering, are both
 * states in which something IS broken, so the fields minted below are `bad`. Same closed
 * vocabulary (`STATES` in `payload.py`), same two-word `value` (`ENGINE_STATES`), different
 * judgement -- which is exactly what a separate `state` field is for.
 */

/**
 * One value, ready to place. The client's mirror of `keel/web/payload.py`'s `Field` TypedDict:
 * `value` is machine input, `display` is the human's, `state` is the judgement.
 *
 * `state` is typed as a plain `string` rather than a union of the five words in `payload.STATES`.
 * Deliberate: a union here would make an unknown state a TYPE error at author time and a silent
 * `undefined` class at run time, and the run-time behaviour is what matters -- `render.js`'s
 * `STATE_CLASS` lookup falls back to no class for a word it does not know, which is the correct
 * response to a server one version ahead of this page.
 *
 * @typedef {{value: string, display: string, state: string}} Field
 */

/**
 * A refusal or a failure, as `payload.error_envelope` writes it. `status` is a STRING because
 * every number in this contract is (see that function's docstring: "a payload with 'only a few'
 * numbers in it needs a per-field rule about which ones, and that rule is what rots").
 *
 * @typedef {{status: string, title: string, detail: string}} ApiError
 */

/**
 * One answer from `/api/*`, in the one shape every caller sees.
 *
 * @typedef {object} Reading
 * @property {string}       as_of   ISO-8601 instant the answer was built at, or `""`.
 * @property {Field}        engine  Whether there are figures to show, and what to say if not.
 * @property {any}          data    The endpoint's payload, or `null` for nothing to render.
 * @property {ApiError|null} error  Present only when the request failed or was refused.
 * @property {any}          sort    The sort echo, or `null`. Unused here; #537's tables read it.
 */

/**
 * Wire key names are kept EXACTLY as the server spells them -- `as_of`, not `asOf`.
 *
 * The camelCase rename is the reflex and it is refused here for one reason: someone debugging
 * this page has the Network tab open beside the source, and a rename means every field has two
 * names and the reader has to hold the mapping. keel's whole argument for the browser is that
 * what runs is what you read; a translation layer between the two is a small, permanent tax on
 * exactly that.
 */

/** The API's prefix. One string, spelled once. @type {string} */
const API_PREFIX = "/api/";

/**
 * `X-Keel-Client: 1`, on every request including the GETs that do not require it.
 *
 * `server.py::_api_client_header_ok` checks this header on `POST /api/*` only, and its docstring
 * explains why a GET is not the gap it closes. It is sent on every request anyway, and the
 * reason is the failure mode of the alternative: a client that adds the header only where it is
 * checked is a client where the first write ever written is the one that has to remember. It
 * costs nothing on a same-origin request -- the custom header would force a CORS preflight
 * cross-origin, which is the whole defence, and `connect-src 'self'` means there is no
 * cross-origin request to preflight.
 *
 * @type {Record<string, string>}
 */
const HEADERS = { "X-Keel-Client": "1", Accept: "application/json" };

/**
 * A `Field` for a state this client had to mint because no server document carried one.
 *
 * @param {string} display
 * @returns {Field}
 */
function stopped(display) {
  return { value: "stopped", display: display, state: "bad" };
}

/**
 * Read one endpoint.
 *
 * @param {string} endpoint                 an endpoint name, e.g. `"status"` -- NOT a full path.
 * @param {Record<string, string>} [params] query parameters, e.g. `{sort: "qty", dir: "desc"}`.
 * @returns {Promise<Reading>}              never rejects; every failure is a `Reading`.
 */
export async function read(endpoint, params) {
  const url = new URL(API_PREFIX + endpoint, window.location.origin);
  if (params) {
    for (const [name, value] of Object.entries(params)) url.searchParams.set(name, value);
  }

  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: HEADERS,
      // The session cookie is `HttpOnly` and `SameSite=Strict`; without it every request is a
      // 403. `same-origin` is `fetch`'s default and is spelled out anyway, because it is the
      // line whose omission would break the entire page in a way that looks like an auth bug.
      credentials: "same-origin",
      // Belt to the server's own `Cache-Control: no-store`. The spec routes `/api/*` as
      // `NetworkOnly`, "no exceptions", because "opening the app to last week's equity styled as
      // current is worse than an error" -- this is the request-side half of that, and it holds
      // whether or not a service worker (#538) is installed.
      cache: "no-store",
      redirect: "error",
    });
  } catch (cause) {
    // Case 4. `fetch` rejects on a transport failure and on nothing else -- a 500 is a resolved
    // promise. So reaching here means there was no answer at all, and the honest sentence is
    // about the SERVER, not about the deployment: `keel serve` is what stopped.
    return {
      as_of: "",
      engine: stopped("keel isn't running — the local server at this address did not answer"),
      data: null,
      error: {
        status: "0",
        title: "No answer",
        // `String(cause)` and no more: a `TypeError: Failed to fetch` is all a browser gives for
        // a refused connection anyway, and anything richer here would be invented.
        detail: String(cause),
      },
      sort: null,
    };
  }

  let document_;
  try {
    document_ = await response.json();
  } catch (cause) {
    // Every `/api/*` response is JSON, refusals included -- `server.py::_refuse` routes by PATH
    // precisely so a `fetch` client never has to parse an HTML error page. So a parse failure
    // here is not a normal outcome; it means something is answering on this port that is not
    // keel, which is worth saying rather than swallowing.
    return {
      as_of: "",
      engine: stopped("Something on this port answered, but not with keel's API"),
      data: null,
      error: { status: String(response.status), title: "Unreadable answer", detail: String(cause) },
      sort: null,
    };
  }

  const as_of = typeof document_.as_of === "string" ? document_.as_of : "";

  if (!response.ok) {
    // Case 3. The error document carries no `engine`, so one is minted -- and `"stopped"` is the
    // right word for it despite the server being plainly up. `payload.ENGINE_STATES`'s own
    // comment settles this: a third word for "the report raised" was drafted and dropped because
    // "the CLIENT behaviour required by a stopped engine and by an unbuildable report is
    // identical -- show no figures, say why". This is the client that comment is about.
    const error = /** @type {ApiError} */ (
      document_.error || { status: String(response.status), title: "Refused", detail: "" }
    );
    return { as_of: as_of, engine: stopped(error.title), data: null, error: error, sort: null };
  }

  // Cases 1 and 2. `engine` is validated rather than trusted: this page is served from the same
  // process as the API, so a mismatch should be impossible -- but a page cached by a browser
  // across an engine upgrade is exactly the skew the spec's service-worker cache key (#538)
  // exists to prevent one layer up, and "impossible" is not a reason to render `undefined`.
  const engine = isField(document_.engine)
    ? document_.engine
    : stopped("keel's answer carried no engine state");

  return {
    as_of: as_of,
    engine: engine,
    // `?? null`, never `|| null`: `data` may legitimately be `0`, `""` or `false` for some future
    // endpoint, and `||` would rewrite all three into "nothing to render".
    data: document_.data ?? null,
    error: null,
    sort: document_.sort ?? null,
  };
}

/**
 * Whether a value has the three keys of a `Field`, all strings.
 *
 * @param {any} value
 * @returns {value is Field}
 */
function isField(value) {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof value.value === "string" &&
    typeof value.display === "string" &&
    typeof value.state === "string"
  );
}
