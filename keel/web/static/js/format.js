// @ts-check

/**
 * `Intl.DateTimeFormat` wrappers. Dates only -- money is formatted server-side (#533).
 *
 * ── THIS MODULE IS 40 LINES BECAUSE THERE IS ALMOST NOTHING FOR IT TO DO ─────────────────────
 *
 * Measured against `keel/web/payload.py` rather than assumed: every instant that reaches this
 * client as DATA already carries a `display` string. `payload.moment()` builds it (`_gmt(ts,
 * "%Y-%m-%d %H:%M:%S")` plus ` UTC`), and `render.js` places it verbatim like every other
 * `display`. `opened_at`, `last_candle_at`, `attested_at`, `updated_at`, `lapses_at` -- all of
 * them arrive formatted.
 *
 * Exactly one instant on the wire is unformatted: `envelope.as_of`, which `payload.iso()`
 * writes as a bare ISO-8601 string (`2025-06-04T01:20:00Z`) and which is deliberately present
 * even when `data` is `null`, because -- in the envelope's own words -- "a client showing 'keel
 * isn't running' should be able to say since when it was looking." Formatting that one string
 * is this module's whole job.
 *
 * The spec's file list names `format` as a module, so it is a module rather than a function
 * hidden inside `render.js`: it is the seam every later date decision lands on, and #537's seven
 * views should find it already there.
 *
 * ── WHAT THIS MODULE DELIBERATELY DOES NOT OFFER: RELATIVE TIME ──────────────────────────────
 *
 * `Intl.RelativeTimeFormat` is right there, and "updated 12 seconds ago" is what a dashboard
 * usually wants. It is not offered, because producing it means subtracting `as_of` from the
 * browser's clock -- the client deriving a displayed value, which is the one thing the data
 * contract forbids (§"The data contract": "The client places them; it never derives them").
 *
 * It would also be WRONG here in a way that is easy to miss: the subtraction spans two clocks.
 * `as_of` comes from the machine running the engine and `Date.now()` from the machine running
 * the browser, and on a laptop whose clock has drifted the answer is "updated in 4 minutes".
 *
 * The reversal condition: if an elapsed figure is wanted, `keel/web/payload.py` already has
 * `duration()` and already emits `age` on every freshness row using it. The fix is to add the
 * field there, where one clock is in play and a `Decimal` is in scope -- not a subtraction here.
 */

/**
 * The page's own language, used as the formatting locale.
 *
 * Reading `<html lang>` rather than passing `undefined` (the reader's OS locale) keeps the page
 * internally consistent: every string on it comes from Python and is English today, and a
 * French-locale reader should not get `04/06/2025` sitting beside English prose and an English
 * `2025-06-04 01:20:00 UTC` from the server. When the interface is localised -- the spec
 * anticipates it, "changing the prefix is a one-line change if the interface is ever localised"
 * -- the dates follow the page's language automatically, from this one line.
 *
 * @type {string|undefined}
 */
const LOCALE = document.documentElement.lang || undefined;

/**
 * The one formatter, built once at module load rather than per call.
 *
 * `Intl.DateTimeFormat` construction is the expensive half of `Intl` (it resolves locale data);
 * `format()` is cheap. This page rebuilds its banner every 15 seconds, so a formatter
 * constructed inside `instant()` would be constructed four times a minute forever, for a value
 * that never varies.
 *
 * `hour12: false` explicitly: the 12-hour clock is the `en-US` default, and `01:20` next to a
 * `1:20:00 AM` from a different code path is the kind of disagreement nobody reports and
 * everybody misreads.
 *
 * @type {Intl.DateTimeFormat}
 */
const STAMP = new Intl.DateTimeFormat(LOCALE, {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "UTC",
  timeZoneName: "short",
});

/**
 * `as_of`, for a human.
 *
 * **Pinned to UTC**, with the zone named by `Intl` rather than appended by hand. keel's day
 * boundaries are UTC everywhere -- gates, scoping, the activity feed -- and `render.py:utc()`
 * records what rendering in local time cost the last time it was tried: #381, where the gate
 * said one day and the rendering said another and a "today" view could be permanently empty.
 * Every other timestamp on this page is a server-built UTC string; one local-time stamp among
 * them would reintroduce exactly that class of mistake, cosmetically this time and then not.
 *
 * The spelling differs from the server's (`06/04/2025, 01:20:00 UTC` in `en-US` against
 * `2025-06-04 01:20:00 UTC`), and that is accepted rather than fixed. *Forcing `en-CA` to mimic
 * the server's ISO-ish order was considered and rejected*: it hardcodes one locale's
 * conventions for every reader in order to win a cosmetic match, and it is the sort of choice
 * that is invisible until the interface is localised and then has to be undone. Both spellings
 * name the zone, which is the part that carries meaning.
 *
 * @param {string} iso  an ISO-8601 instant, or `""`
 * @returns {string}    a formatted instant, or `""` for nothing renderable
 */
export function instant(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  // An invalid `Date` yields `NaN` from `getTime()`. Checked rather than trusted: `as_of` is
  // built by `payload.iso()`, which returns `""` for an unrenderable timestamp -- but this
  // function is the client's only date entry point, and a parse failure must degrade to "no
  // stamp" rather than to the literal string "Invalid Date" on a trading dashboard.
  if (Number.isNaN(when.getTime())) return "";
  return STAMP.format(when);
}
