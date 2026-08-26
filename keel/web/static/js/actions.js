// @ts-check
/**
 * The write boundary: this session's write token, the actions performed through it, and what each
 * one reported.
 *
 * ── WHY THIS MODULE EXISTS, AND WHOSE SHAPE IT IS ───────────────────────────────────────────
 * The design spec's reference implementation is
 * [youperiod.app](https://github.com/getify/youperiod.app), and its client is five modules with
 * one job each: `main.js` attaches event listeners, `data-manager.js` owns the storage boundary
 * behind a `get`/`set` pair, `utils.js` holds shared helpers. keel's client was built to that
 * shape -- `api.js` is the single `fetch` wrapper, `render.js` the only view builder,
 * `format.js` the helpers -- and then #540 gave the browser a WRITE surface and put all of it in
 * `main.js`: the token, the submit handler, the outcome memory, and the sentence-picking. That
 * is the module the reference keeps emptiest, and it had grown a second job.
 *
 * So this is keel's `data-manager.js`. It owns everything about performing an action except the
 * two things that are not its business: the `fetch` (that is `api.js`, still the only module
 * that opens a connection) and the DOM (that is `render.js` and the listener in `main.js`).
 *
 * ── WHAT IT HIDES ───────────────────────────────────────────────────────────────────────────
 * A caller needs to know an action's key and the operator's answers. It does not need to know
 * that a write carries a session-scoped HMAC token in `X-Keel-CSRF` beside `X-Keel-Client`, that
 * the token arrives on `/api/setup` and nowhere else, that the result document distinguishes
 * "done" from "already done" through a `changed` flag rather than an error, or that the sentence
 * to show comes from `data.message.display` on success and `error.detail` on refusal. All of
 * that is here, and none of it is in the listener.
 *
 * ── THE MEMORY IS SESSION STATE, AND IT IS THE ONLY STATE THIS CLIENT KEEPS ─────────────────
 * Every other thing on screen is a server document re-read on a timer. "What did I just do" is
 * the one fact no document can answer: `paint` rebuilds the view from `/api/*` on every poll,
 * every tick and after every action, so an outcome written into a card is gone at the next
 * rebuild. It is deliberately NOT persisted and dies with the page -- a message about an action
 * from an hour ago, presented as current, is the same failure #538 refuses to make with a cached
 * balance, in a much milder register.
 */

import { runAction } from "./api.js";

/**
 * @typedef {import("./api.js").Reading} Reading
 */

/**
 * This session's write token, from `/api/setup`'s `data.csrf`.
 *
 * `""` until a setup document has been read, and that is correct rather than a gap: the only
 * thing that can submit an action is a form this client drew, and it draws them only on the view
 * that just supplied the token. An empty token reaches the server and is refused, which is the
 * right answer for a submission that could not have come from a rendered action card.
 *
 * @type {string}
 */
let token = "";

/**
 * What each action last reported, by action key.
 * @type {Map<string, string>}
 */
const results = new Map();

/**
 * Take the write token off a `/api/setup` document.
 *
 * Called from the view that reads it, once per read. `payload.setup_payload` sends it as a bare
 * string rather than a `Field` precisely because it is a credential the client SENDS and never
 * displays; treating it as one here means it never reaches `render.js`.
 *
 * @param {any} data  `/api/setup`'s `data`, or `null`.
 */
export function remember(data) {
  token = data && typeof data.csrf === "string" ? data.csrf : "";
}

/**
 * Whether a write can be attempted at all.
 *
 * Exported so a caller can ask rather than infer from a failure. Nothing uses it to HIDE a
 * button -- "a client that hides a button is not a gate" is the spec's own sentence, and the
 * server refuses what is not in `keel.commands.setup.ACTIONS` regardless of what this returns.
 *
 * @returns {boolean}
 */
export function available() {
  return token !== "";
}

/**
 * Perform one declared action and record what it reported.
 *
 * @param {string} key                     an action key from `/api/setup`'s `actions`.
 * @param {Record<string, string>} values  the declared fields, by name.
 * @returns {Promise<string>}              the sentence to show. Never rejects.
 */
export async function perform(key, values) {
  const reading = await runAction(key, values, token);
  const outcome = describe(reading);
  results.set(key, outcome);
  return outcome;
}

/**
 * What an action last reported, or `""` if it has not been run in this session.
 *
 * @param {string} key
 * @returns {string}
 */
export function outcomeFor(key) {
  return results.get(key) ?? "";
}

/**
 * Every recorded outcome, for a view being rebuilt.
 *
 * A copy, not the live map: a caller iterating this while an action completes would otherwise be
 * iterating a collection that changed underneath it.
 *
 * @returns {Map<string, string>}
 */
export function recorded() {
  return new Map(results);
}

/**
 * The sentence for a finished action -- the SERVER's own words in every branch.
 *
 * `data.message.display` when it ran, `error.detail` when it did not. This function chooses which
 * field to read and never composes a sentence of its own, which is the same rule `render.js`
 * follows for every value on screen, applied to the one outcome that arrives outside a view.
 *
 * **`changed` is appended because it is not a success flag.** Every action is idempotent, so a
 * repeated submission succeeds and reports `already done -- nothing to change`, which is a true
 * statement about the deployment rather than a soft failure. Dropping it would make a re-run look
 * identical to a first run, and the difference is the whole reason `keel.commands.setup` carries
 * the field.
 *
 * @param {Reading} reading
 * @returns {string}
 */
function describe(reading) {
  const data = reading.data;
  if (data && data.message && typeof data.message.display === "string") {
    const changed = data.changed && data.changed.display ? data.changed.display : "";
    return changed ? data.message.display.concat(" — ", changed) : data.message.display;
  }
  const error = reading.error;
  const detail = error && error.detail ? error.detail : "";
  const title = error && error.title ? error.title : reading.engine.display;
  return detail ? title.concat(" — ", detail) : title;
}
